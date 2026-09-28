"""单任务子进程执行入口（实验方案 §7.E1 流程）。

每个实验单元 = 一个子进程：初始化环境 →（PR 预灌偏好）→ 安装隐私策略与
审计 → 运行 agent → 终态快照 → oracle 判定 → 落盘结果（含审计、token、
延迟）。进程退出即销毁 C2/C3 映射表（session 生命周期）。

用法：
  python -m experiments.privacy_v3.run_unit --config C3 --task-id h1_dc1 \
      --dataset <svrbench_dataset.json> --out <unit_result.json>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from urllib.request import Request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]  # iot-agent-refactored_demo
GIT_ROOT = REPO_ROOT.parent                     # 仓库根（try.memory 所在）
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(GIT_ROOT))

BASE_URL = "http://127.0.0.1:8123"


def _post(path: str, payload: dict | None = None, timeout: float = 15.0):
    data = json.dumps(payload or {}).encode() if payload is not None else b"{}"
    req = Request(f"{BASE_URL}{path}", data=data,
                  headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _load_runtime_with_fresh_db(db_path: Path):
    """为 PR 任务替换全局记忆 runtime，使用任务专属 SQLite。"""
    from smartHome.m_agent.memory import runtime_v1

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    runtime_v1._RUNTIME = runtime_v1.DemoMemoryRuntime(db_path=str(db_path))
    return runtime_v1._RUNTIME


def seed_preferences(runtime, task: dict, entity_to_device: dict[str, str]):
    """构造 MemoryRecord 偏好并写入（scope=device 绑定目标设备）。"""
    import importlib
    from datetime import datetime, timezone

    try_memory = importlib.import_module("try.memory")
    try_confidence = importlib.import_module("try.memory.confidence")

    now = datetime.now(timezone.utc)
    for i, seed in enumerate(task.get("pr_seed") or []):
        entity_id = seed.get("device_entity", "")
        record = try_memory.MemoryRecord(
            memory_id=f"pref_{task['task_id']}_{i}",
            scope="device",
            device_id=entity_to_device.get(entity_id),
            room_id=None,
            memory_type="preference",
            subject=seed.get("subject", "用户偏好"),
            predicate="preference",
            object=seed.get("natural_text", ""),
            condition=None,
            action=None,
            natural_text=seed.get("natural_text", ""),
            structured_payload={},
            source="user_explicit",
            evidence_refs=[try_memory.EvidenceRef(ref_type="doc",
                                                  ref_id=f"pr_seed_{task['task_id']}",
                                                  timestamp=now)],
            confidence=try_confidence.get_source_authority("user_explicit"),
            importance=0.8,
            half_life_days=try_confidence.get_default_half_life("preference"),
            created_at=now,
            updated_at=now,
            valid_from=now,
            status="active",
        )
        runtime.service.upsert_memory_record(record)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, choices=["C0", "C1", "C2", "C3"])
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    task = next(t for t in dataset["dev_tasks"] + dataset["test_tasks"]
                if t["task_id"] == args.task_id)
    env_id = dataset["env_ids"][task["home_id"]]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    error: str | None = None
    agent_answer = ""
    result: dict = {"task_id": task["task_id"], "config": args.config,
                    "category": task["category"], "home_id": task["home_id"]}

    try:
        # 1. 初始化家庭环境并验证（§7.E1 流程 1）
        init_resp = _post("/api/mock/init_env", {"env_id": env_id})
        assert init_resp.get("status") == "initialized", init_resp

        # 2. PR：任务专属记忆库 + 冻结偏好
        runtime = None
        if task.get("pr_seed"):
            db_path = Path("/tmp") / f"svrbench_mem_{task['task_id']}_{args.config}.sqlite3"
            runtime = _load_runtime_with_fresh_db(db_path)
            env_yaml = REPO_ROOT / "experiments" / "privacy_v3" / "data" / "envs" / f"{env_id}.yaml"
            env_def = json.loads(env_yaml.read_text(encoding="utf-8"))
            entity_to_device = {ent["entity_id"]: ent["device_id"] for ent in env_def["entities"]}
            runtime._seeded = True  # 跳过默认 demo seed，仅使用冻结偏好
            runtime.ensure_ready()  # 仍执行 HA 事实同步与维护
            seed_preferences(runtime, task, entity_to_device)

        # 3. 安装策略 + 审计。
        #    检测器不接触任务金标：敏感词典只来自系统自身检测层（本地正则 +
        #    隐私 LLM 兜底）。金标 spans 只交给审计器度量 G/L（TLR/EPR 分子
        #    分母），E2 的检测器 P/R/F1 因此反映真实检出能力（方案 §4.4.3）。
        from experiments.privacy_v3 import audit

        strategy = audit.build_strategy(args.config)
        recorder = audit.install(strategy, task["sensitive_spans"])

        # 4. 初始快照
        from experiments.privacy_v3 import oracle
        entity_ids = [s["value"] for s in task["sensitive_spans"]
                      if s["field_type"] == "entity_id"]
        init_snapshot = oracle.snapshot_all(entity_ids)

        # 5. 运行 agent（计时包在审计 wrapper 外层，两者都生效）
        encode_latencies: list[float] = []
        from smartHome.m_agent.agent.hooks import langchain_middleware as mw
        audited_encode = mw.encode_messages  # audit.install 安装的带审计版本

        def timed_encode(messages):
            t0 = time.perf_counter()
            out = audited_encode(messages)
            encode_latencies.append(time.perf_counter() - t0)
            return out

        mw.encode_messages = timed_encode

        from smartHome.m_agent.agent.base_home_easy_agent import run_easy_ourAgent
        t_agent = time.perf_counter()
        agent_answer = run_easy_ourAgent(task["instruction"]) or ""
        agent_latency = time.perf_counter() - t_agent

        # 6. 终态快照 + 判定
        final_snapshot = oracle.snapshot_all(entity_ids)
        verdict = oracle.judge(task, final_snapshot=final_snapshot,
                               init_snapshot=init_snapshot,
                               agent_answer=agent_answer,
                               tool_calls=recorder.tool_calls)

        # 7. token 统计 + 汇总
        from smartHome.m_agent.common.token_tracker import get_token_tracker
        token_summary = get_token_tracker().summary()

        audit_summary = recorder.summary()
        total_g = audit_summary["total_sensitive_spans_pre"]
        total_l = audit_summary["total_sensitive_spans_leaked"]

        # E2：检测器命中集合（与替换策略无关，按策略实例落盘）
        if args.config == "C3":
            from smartHome.m_agent.agent.utils import privacy_codex as _pc
            detector_detections = sorted(_pc._ENCODE_MAP.keys())
        elif hasattr(strategy, "detections"):
            detector_detections = sorted(strategy.detections)
        else:
            detector_detections = []

        result.update({
            "env_id": env_id,
            "instruction": task["instruction"],
            "agent_answer": agent_answer,
            "verdict": verdict,
            "tsr": verdict["success"],
            "leak": {"G": total_g, "L": total_l,
                     "task_leaked": total_l > 0},
            "audit": {
                "cloud_request_count": audit_summary["cloud_request_count"],
                "sensitive_hits_by_request": [
                    {"seq": r["seq"], "hits": r["sensitive_hits"]}
                    for r in recorder.requests
                ],
                "detector_detections": detector_detections,
            },
            "encode_latencies": encode_latencies,
            "sanitize_total_s": round(sum(encode_latencies), 4),
            "agent_wall_s": round(agent_latency, 2),
            "tokens": token_summary,
            "tool_calls": recorder.tool_calls,
        })
    except Exception as exc:  # noqa: BLE001
        import traceback
        error = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        result.update({"tsr": False, "error": error,
                       "verdict": {"success": False, "checks": []}})
    finally:
        try:
            _post("/api/mock/original_env")
        except Exception:
            pass

    result["wall_s"] = round(time.time() - started_at, 2)
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
    print(json.dumps({"task_id": result["task_id"], "config": args.config,
                      "tsr": result.get("tsr"), "error": error},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
