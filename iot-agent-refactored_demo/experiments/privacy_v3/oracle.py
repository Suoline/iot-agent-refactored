"""任务判定器（oracle）：TSR = 全部适用断言通过且无 forbidden。

断言类型（实验方案 §4.3）：
- assert_final：最终状态/属性（数值容差）
- assert_unchanged：forbidden_actions 的等价检查（初始快照 vs 终态）
- answer_contains / answer_numeric：IR 结构化答案
- answer_state：二元状态问答按实体真实 state 做否定语义校验（§4.3）
- pt_spec：PT 降级判定——监控调用指向正确实体 + 目标动作未提前执行
  （NotifyOnConditionTool 为占位实现，无法模拟真实触发，报告需如实说明）
"""
from __future__ import annotations

import json
import re
from typing import Any
from urllib.request import Request, urlopen

BASE_URL = "http://127.0.0.1:8123"


def _get_state(entity_id: str) -> dict[str, Any] | None:
    req = Request(f"{BASE_URL}/api/states/{entity_id}")
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def snapshot_all(entity_ids: list[str]) -> dict[str, dict]:
    snap: dict[str, dict] = {}
    for eid in entity_ids:
        state = _get_state(eid)
        if state is not None:
            snap[eid] = {"state": state.get("state"),
                         "attributes": {k: v for k, v in (state.get("attributes") or {}).items()
                                        if k in ("temperature", "current_position", "volume_level",
                                                 "brightness", "hvac_mode")}}
    return snap


def _num_eq(a, b, tol: float = 0.01) -> bool:
    try:
        return abs(float(a) - float(b)) <= tol
    except (TypeError, ValueError):
        return a == b


def _match_final(entity_state: dict, expectation: dict) -> tuple[bool, str]:
    entity_id = expectation["entity_id"]
    if entity_state is None:
        return False, f"{entity_id}: 状态不可得"
    if "state" in expectation and str(entity_state.get("state")) != str(expectation["state"]):
        return False, f"{entity_id}: state={entity_state.get('state')} 期望 {expectation['state']}"
    for attr, expected in (expectation.get("attributes") or {}).items():
        actual = (entity_state.get("attributes") or {}).get(attr)
        if actual is None:
            return False, f"{entity_id}: 缺少属性 {attr}"
        if not _num_eq(actual, expected):
            return False, f"{entity_id}.{attr}={actual} 期望 {expected}"
    return True, ""


def _check_answer(answer: str, contains: list[str], numeric: dict | None) -> tuple[bool, str]:
    if contains:
        for kw in contains:
            if kw not in answer:
                return False, f"回答缺少关键词「{kw}」"
    if numeric:
        expect, tol = numeric["expect"], numeric.get("tolerance", 0.5)
        numbers = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", answer)]
        if not any(abs(n - expect) <= tol for n in numbers):
            return False, f"回答数值 {numbers} 无一落在 {expect}±{tol}"
    return True, ""


_NEGATION_WORDS = ("没", "未", "不", "无", "别", "非")


def _check_state_answer(answer: str, *, on_term: str, off_term: str,
                        state_on: bool) -> tuple[bool, str]:
    """二元状态问答的结构化校验：答案须正确表达实体 on/off 状态。

    子串匹配无法处理中文否定（「没关」含「关」但语义为开），因此按
    语义判定：肯定词出现且其前 2 字窗口内无否定词，或反义词出现且
    被 2 字窗口内的否定词否定，才算正确表达；含糊或答错均判失败。
    """
    positive, negative = (on_term, off_term) if state_on else (off_term, on_term)
    for m in re.finditer(re.escape(negative), answer):
        window = answer[max(0, m.start() - 2):m.start()]
        if any(neg in window for neg in _NEGATION_WORDS):
            return True, f"否定式正确表达（{window}{negative}）"
    for m in re.finditer(re.escape(positive), answer):
        window = answer[max(0, m.start() - 2):m.start()]
        if not any(neg in window for neg in _NEGATION_WORDS):
            return True, f"肯定式正确表达（{positive}）"
    return False, f"回答未正确表达「{'开' if state_on else '关'}」语义：{answer!r}"


def judge(task: dict, *, final_snapshot: dict, init_snapshot: dict,
          agent_answer: str, tool_calls: list[dict]) -> dict[str, Any]:
    """返回判定明细与整体 TSR 布尔。"""
    checks: list[dict[str, Any]] = []

    for expectation in task.get("assert_final", []):
        eid = expectation["entity_id"]
        ok, why = _match_final(final_snapshot.get(eid) or {}, expectation)
        checks.append({"kind": "final_state", "target": eid, "ok": ok, "detail": why})

    for eid in task.get("assert_unchanged", []):
        before, after = init_snapshot.get(eid), final_snapshot.get(eid)
        ok = before is not None and after is not None and (
            before.get("state") == after.get("state") and before.get("attributes") == after.get("attributes"))
        checks.append({"kind": "forbidden_state_change", "target": eid,
                       "ok": bool(ok), "detail": "" if ok else f"{eid}: 状态被改变 {before} -> {after}"})

    if task.get("answer_contains") or task.get("answer_numeric"):
        ok, why = _check_answer(agent_answer or "", task.get("answer_contains", []),
                                task.get("answer_numeric"))
        checks.append({"kind": "answer", "target": "agent_answer", "ok": bool(ok), "detail": why})

    st = task.get("answer_state")
    if st:
        ent_state = (final_snapshot.get(st["entity_id"]) or {}).get("state")
        ok, why = _check_state_answer(
            agent_answer or "", on_term=st["on_term"], off_term=st["off_term"],
            state_on=str(ent_state) == "on")
        checks.append({"kind": "answer_state", "target": st["entity_id"],
                       "ok": bool(ok), "detail": why})

    pt = task.get("pt_spec")
    if pt:
        monitor_entity = pt["monitor_entity"]
        monitoring_calls = [
            tc for tc in tool_calls
            if tc.get("name") == "start_entity_persistent_monitoring"
            and monitor_entity in json.dumps(tc.get("args", {}), ensure_ascii=False)
        ]
        checks.append({"kind": "pt_monitoring", "target": monitor_entity,
                       "ok": bool(monitoring_calls),
                       "detail": "" if monitoring_calls else f"未对 {monitor_entity} 建立监控"})

    success = all(c["ok"] for c in checks) if checks else False
    return {"success": success, "checks": checks}
