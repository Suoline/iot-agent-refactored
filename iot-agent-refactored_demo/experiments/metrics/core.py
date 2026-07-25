from __future__ import annotations

from datetime import datetime
from statistics import mean


CORRECTION_SCENARIOS = {"A2", "A5", "B4", "D1", "D2", "D3"}


def _top_grounding_memory(task_trace: dict) -> dict | None:
    ranked = []
    for step in task_trace.get("steps", []):
        for item in step.get("retrieved_memories", []):
            if item.get("in_grounding_set") or item.get("in_usable_set"):
                ranked.append(item)
    ranked.sort(key=lambda row: row.get("rank", 999999))
    return ranked[0] if ranked else None


def _ece_rows(task_traces: list[dict], bins: int = 10) -> float | None:
    samples = []
    for trace in task_traces:
        top = _top_grounding_memory(trace)
        if not top or trace.get("task_success") is None:
            continue
        confidence = float(top.get("effective_confidence", 0.0))
        samples.append((confidence, 1.0 if trace["task_success"] else 0.0))
    if not samples:
        return None
    bucket_rows: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for confidence, success in samples:
        index = min(bins - 1, max(0, int(confidence * bins)))
        bucket_rows[index].append((confidence, success))
    total = len(samples)
    return sum(
        (len(rows) / total)
        * abs(mean(item[1] for item in rows) - mean(item[0] for item in rows))
        for rows in bucket_rows
        if rows
    )


def _uc_for_trace(task_trace: dict) -> float | None:
    if task_trace.get("scenario_id") not in CORRECTION_SCENARIOS:
        return None
    statuses = task_trace.get("memory_status_after", {})
    old_invalidated = any(value in {"superseded", "expired"} for value in statuses.values())
    new_used = any(
        item.get("in_usable_set")
        for step in task_trace.get("steps", [])
        for item in step.get("retrieved_memories", [])
    )
    return 1.0 if old_invalidated and new_used else 0.0


def _memory_precision_counts(task_trace: dict) -> tuple[int, int]:
    committed_ids = {
        record["memory_id"]
        for record in task_trace.get("memory_records_after", [])
        if record.get("status") not in {"candidate", "deleted"}
    }
    helpful_ids = {
        event["memory_id"]
        for event in task_trace.get("usage_events", [])
        if event.get("memory_id") in committed_ids
        if event.get("contribution") == "helpful" and event.get("outcome") == "success"
    }
    return len(helpful_ids), len(committed_ids)


def _dead_memory_counts(task_trace: dict) -> tuple[int, int]:
    records = task_trace.get("memory_records_after", [])
    if not records:
        return 0, 0
    now = datetime.fromisoformat(task_trace["sim_time"])
    dead = 0
    for record in records:
        created_at = datetime.fromisoformat(record["created_at"])
        age_days = (now - created_at).total_seconds() / 86400.0
        if int(record.get("access_count", 0)) == 0 and age_days > float(record.get("half_life_days", 0)):
            dead += 1
    return dead, len(records)


def _dmr_for_trace(task_trace: dict) -> float | None:
    dead, total = _dead_memory_counts(task_trace)
    return dead / total if total else None


def _resampling_recall_counts(task_trace: dict) -> tuple[int, int]:
    resampled_ids = {
        memory_id
        for event in task_trace.get("maintenance_events", [])
        for memory_id in event.get("resampled_memory_ids", [])
    }
    if not resampled_ids:
        return 0, 0
    recalled_ids = {
        item.get("memory_id")
        for step in task_trace.get("steps", [])
        for item in step.get("retrieved_memories", [])
        if item.get("in_usable_set") and item.get("runtime_status") == "active"
    }
    return len(resampled_ids & recalled_ids), len(resampled_ids)


def _rrr_for_trace(task_trace: dict) -> float | None:
    recalled, resampled = _resampling_recall_counts(task_trace)
    return recalled / resampled if resampled else None


def _stale_retrieval_counts(task_trace: dict) -> tuple[int, int]:
    steps = task_trace.get("steps", [])
    stale_hits = sum(
        1
        for step in steps
        if any(
            item.get("true_status") in {"expired", "superseded"} and item.get("in_usable_set")
            for item in step.get("retrieved_memories", [])
        )
    )
    return stale_hits, len(steps)


def _task_success_for_trace(task_trace: dict) -> float | None:
    if task_trace.get("task_success") is not None:
        return 1.0 if task_trace["task_success"] else 0.0
    if task_trace.get("outcome") is not None:
        return 1.0 if task_trace["outcome"] == "success" else 0.0
    return None


def _action_assertions(task_trace: dict) -> list[dict]:
    return [
        item
        for item in task_trace.get("assertion_results", [])
        if item.get("kind") == "action"
        and (item.get("expected") or {}).get("service") not in {None, "memory.answer"}
    ]


def task_metrics(task_trace: dict) -> dict[str, float | None]:
    tsr = _task_success_for_trace(task_trace)
    stale_hits, retrieval_step_count = _stale_retrieval_counts(task_trace)
    srr = stale_hits / retrieval_step_count if retrieval_step_count else None

    action_assertions = _action_assertions(task_trace)
    wrong_device = sum(
        1
        for item in action_assertions
        if (item.get("observed") or {}).get("entity_id")
        != (item.get("expected") or {}).get("entity_id")
    )
    wdr = wrong_device / len(action_assertions) if action_assertions else None
    pm = (
        sum(1 for item in action_assertions if item.get("success")) / len(action_assertions)
        if action_assertions
        else None
    )
    helpful, committed = _memory_precision_counts(task_trace)
    final_state_success = task_trace.get("final_state_success")
    estimated_prompt_tokens = float(task_trace.get("estimated_prompt_tokens", 0))
    return {
        "TSR": tsr,
        "State TSR": (
            1.0 if final_state_success else 0.0
            if final_state_success is not None
            else None
        ),
        "task_success": tsr,
        "action_success": _optional_bool(task_trace.get("action_success")),
        "clarification_success": _optional_bool(task_trace.get("clarification_success")),
        "memory_assertion_success": _optional_bool(task_trace.get("memory_assertion_success")),
        "final_state_success": _optional_bool(final_state_success),
        "SRR": srr,
        "WDR": wdr,
        "CB": float(task_trace.get("clarification_turns", 0)),
        "PM": pm,
        "UAA": (
            1.0 if task_trace.get("safety_gated") else 0.0
            if task_trace.get("safety_relevant")
            else None
        ),
        "UC": _uc_for_trace(task_trace),
        "MP": helpful / committed if committed else None,
        "DMR": _dmr_for_trace(task_trace),
        "RRR": _rrr_for_trace(task_trace),
        "Estimated Prompt Tokens": estimated_prompt_tokens,
        "end_to_end_latency_ms": float(task_trace.get("end_to_end_latency_ms", 0)),
        "maintenance_latency_ms": float(task_trace.get("maintenance_latency_ms", 0)),
        "Estimated Maintenance Tokens": float(task_trace.get("estimated_maintenance_tokens", 0)),
    }


def _optional_bool(value) -> float | None:
    if value is None:
        return None
    return 1.0 if value else 0.0


def aggregate_task_metrics(task_traces: list[dict]) -> dict[str, float | None]:
    if not task_traces:
        return {}
    rows = [task_metrics(item) for item in task_traces]
    summary: dict[str, float | None] = {}
    for key in rows[0]:
        values = [row[key] for row in rows if row[key] is not None]
        summary[key] = mean(values) if values else None

    helpful_total = 0
    committed_total = 0
    for trace in task_traces:
        helpful, committed = _memory_precision_counts(trace)
        helpful_total += helpful
        committed_total += committed
    summary["MP"] = helpful_total / committed_total if committed_total else None

    stale_total = 0
    retrieval_step_total = 0
    dead_total = 0
    memory_total = 0
    recalled_total = 0
    resampled_total = 0
    for trace in task_traces:
        stale, steps = _stale_retrieval_counts(trace)
        dead, memories = _dead_memory_counts(trace)
        recalled, resampled = _resampling_recall_counts(trace)
        stale_total += stale
        retrieval_step_total += steps
        dead_total += dead
        memory_total += memories
        recalled_total += recalled
        resampled_total += resampled
    summary["SRR"] = stale_total / retrieval_step_total if retrieval_step_total else None
    summary["DMR"] = dead_total / memory_total if memory_total else None
    summary["RRR"] = recalled_total / resampled_total if resampled_total else None

    prompt_mean = summary.get("Estimated Prompt Tokens")
    tsr = summary.get("TSR")
    summary["Estimated Context Efficiency"] = (
        tsr / max(float(prompt_mean), 1.0) * 1000
        if tsr is not None and prompt_mean is not None
        else None
    )
    # CE needs the B0 aggregate and is computed only in the cross-system report.
    summary["CE"] = None
    summary["ECE"] = _ece_rows(task_traces)
    return summary
