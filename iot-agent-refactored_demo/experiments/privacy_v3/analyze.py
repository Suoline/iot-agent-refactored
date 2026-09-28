"""汇总分析：生成 Table 1（隐私-效用）/ Table 2（检测器）/ Table 3（开销）
与配对 bootstrap 统计（实验方案 §9）。

用法：python -m experiments.privacy_v3.analyze --split test --repeats 3
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from metrics import (config_metrics, detector_prf, load_units, overhead,
                     paired_bootstrap, _task_level)


def _synthesize_missing(results_dir: Path) -> list[dict]:
    """把超时/崩溃（有痕迹但无 finished_at）的单元合成为 tsr=0 失败（§4.3）。

    痕迹来源：`<task>__<cfg>__r<rep>.timeout` 标记文件与已存在但未写完/
    损坏的结果 JSON。合成单元只补 TSR 分母，不带泄漏/开销数据（metrics 侧跳过）。
    """
    import re
    pat = re.compile(r"^(?P<task>.+)__(?P<cfg>C\d+)__r(?P<rep>\d+)$")
    missing: list[dict] = []
    for f in sorted(results_dir.iterdir()):
        m = pat.match(f.stem)
        if not m or f.suffix not in (".json", ".timeout"):
            continue
        if f.suffix == ".json":
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("finished_at"):
                    continue  # 正常完成，load_units 已覆盖
            except Exception:
                pass  # 损坏 JSON → 失败单元
        missing.append({"task_id": m.group("task"), "config": m.group("cfg"),
                        "repeat": int(m.group("rep")),
                        "tsr": False, "missing": "timeout_or_crash",
                        "leak": {"G": 0, "L": 0, "task_leaked": None},
                        "tokens": {}, "encode_latencies": []})
    return missing


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="test")
    parser.add_argument("--results-dir", default="")
    args = parser.parse_args()

    results_dir = Path(args.results_dir) if args.results_dir else HERE / "results" / f"e1_{args.split}"
    dataset = json.loads((HERE / "data" / "svrbench_dataset.json").read_text(encoding="utf-8"))
    category_of = {t["task_id"]: t["category"]
                   for t in dataset["dev_tasks"] + dataset["test_tasks"]}

    units = load_units(results_dir)
    if not units:
        print("无已完成单元")
        return 1
    missing = _synthesize_missing(results_dir)
    if missing:
        print(f"注：{len(missing)} 个超时/崩溃单元按 §4.3 计为失败（不再静默丢分母）")
    bucket = _task_level(units + missing)

    configs = sorted({u["config"] for u in units})
    table1 = [config_metrics(bucket, c, category_of) for c in configs]
    table1 = [t for t in table1 if t]

    stats = [
        paired_bootstrap(bucket, "C0", "C3", "tsr"),
        paired_bootstrap(bucket, "C3", "C2", "tsr"),
        paired_bootstrap(bucket, "C0", "C3", "epr_like") if False else None,
        paired_bootstrap(bucket, "C3", "C2", "tlr"),
        paired_bootstrap(bucket, "C0", "C1", "tsr"),
        paired_bootstrap(bucket, "C1", "C3", "tsr"),
    ]
    # EPR 差值的配对 bootstrap（G/L 加总口径）
    def epr_of(cfg):
        rows = [v for (c, _), v in bucket.items() if c == cfg]
        g = sum(v["G"] for v in rows); l = sum(v["L"] for v in rows)
        return 1 - l / g if g else None
    epr_pairs = []
    for a, b in (("C0", "C3"), ("C3", "C2"), ("C1", "C3")):
        ea, eb = epr_of(a), epr_of(b)
        if ea is not None and eb is not None:
            epr_pairs.append({"pair": f"{a}-{b}", "EPR_diff": round(ea - eb, 4)})

    e2 = detector_prf(units)
    e3 = overhead(units)

    report = {
        "split": args.split,
        "n_units": len(units),
        "n_timeout_or_crash": len(missing),
        "n_tasks": len({u["task_id"] for u in units + missing}),
        "table1_privacy_utility": table1,
        "paired_statistics": [s for s in stats if s],
        "epr_paired_diff": epr_pairs,
        "table2_detector": e2,
        "table3_overhead": e3,
        "notes": [
            "EPR/TLR 只统计 outbound cloud requests；云端响应回显不计入。",
            "TSR 为任务级平均成功率（同任务多 repeat 先平均）。",
            "超时/崩溃单元按 §4.3 计为 TSR 失败；其泄漏与开销数据缺失，不进 TLR/EPR/token 平均。",
            "PT 判定为降级口径：监控调用正确 + 目标动作未提前执行（NotifyOnConditionTool 为占位实现，无法模拟真实触发）。",
            "检测器 P/R/F1 以金标准 span（G>0）为分母，检测命中来自 C1/C2/C3 运行（同一检测器，词典与金标相互独立）。",
        ],
    }
    out = results_dir / "analysis_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # 控制台摘要
    print(f"\n===== Table 1 隐私-效用（{args.split}, {len(units)} units, {report['n_tasks']} tasks）=====")
    print(f"{'配置':<4} {'EPR↑':>8} {'TLR↓':>8} {'TSR↑':>8} {'in_tok/task':>11} {'calls/task':>10}")
    for t in table1:
        print(f"{t['config']:<4} {t['EPR']:>8} {t['TLR']:>8} {t['TSR']:>8} "
              f"{t['tokens_in_per_task']:>11} {t['calls_per_task']:>10}")
    print("\n类别 TSR:")
    for t in table1:
        print(" ", t["config"], t["TSR_by_category"])
    print("\n===== 配对统计（任务级 bootstrap 95% CI）=====")
    for s in report["paired_statistics"]:
        print(f"  {s['pair']} {s['metric']}: diff={s['diff']} CI={s['ci95']} (n={s['n_tasks']})")
    for p in epr_pairs:
        print(f"  {p['pair']} EPR: diff={p['EPR_diff']}")
    print("\n===== Table 2 检测器（E2）=====")
    print(json.dumps(e2, ensure_ascii=False))
    print("\n===== Table 3 开销（E3 摘要）=====")
    print(json.dumps({k: v for k, v in e3.items() if k != "sanitize_latency_by_config"},
                     ensure_ascii=False))
    print(f"\n完整报告: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
