from __future__ import annotations

from statistics import mean


def _top_grounding_memory(task_trace: dict) -> dict | None:
    ranked = []
    for step in task_trace.get("steps", []):
        for item in step.get("retrieved_memories", []):
            if item.get("in_grounding_set") or item.get("in_usable_set"):
                ranked.append(item)
    ranked.sort(key=lambda row: row.get("rank", 999999))
    return ranked[0] if ranked else None


def _ece_rows(task_traces: list[dict], bins: int = 10) -> float:
    samples = []
    for trace in task_traces:
        top = _top_grounding_memory(trace)
        if not top:
            continue
        confidence = float(top.get("effective_confidence", 0.0))
        success = 1.0 if trace.get("outcome") == "success" else 0.0
        samples.append((confidence, success))
    if not samples:
        return 0.0
    bucket_rows: list[list[tuple[float, float]]] = [[] for _ in range(bins)]
    for confidence, success in samples:
        index = min(bins - 1, max(0, int(confidence * bins)))
        bucket_rows[index].append((confidence, success))
    total = len(samples)
    ece = 0.0
    for rows in bucket_rows:
        if not rows:
            continue
        acc = mean(item[1] for item in rows)
        conf = mean(item[0] for item in rows)
        ece += (len(rows) / total) * abs(acc - conf)
    return ece


def _uc_for_trace(task_trace: dict) -> float:
    statuses = task_trace.get("memory_status_after", {})
    if not statuses:
        return 0.0
    old_invalidated = any(value in {"superseded", "expired"} for value in statuses.values())
    new_used = any(
        item.get("in_usable_set")
        for step in task_trace.get("steps", [])
        for item in step.get("retrieved_memories", [])
    )
    return 1.0 if old_invalidated and new_used else 0.0


def _mp_for_trace(task_trace: dict) -> float:
    events = task_trace.get("usage_events", [])
    committed = [
        event for event in events
        if event.get("kind") not in {"assertion_failures", "agent_output"}
    ]
    if not committed:
        return 0.0
    helpful = [
        event for event in committed
        if event.get("contribution") == "helpful" and event.get("outcome") == "success"
    ]
    return len(helpful) / len(committed)


def _dmr_for_trace(task_trace: dict) -> float:
    statuses = task_trace.get("memory_status_after", {})
    if not statuses:
        return 0.0
    archived = sum(1 for value in statuses.values() if value == "archived")
    return archived / max(1, len(statuses))


def _rrr_for_trace(task_trace: dict) -> float:
    statuses = task_trace.get("memory_status_after", {})
    if not statuses:
        return 0.0
    resampled = [
        event for event in task_trace.get("usage_events", [])
        if event.get("kind") == "resampled"
    ]
    if not resampled:
        return 0.0
    revived = sum(1 for event in resampled if statuses.get(event.get("memory_id")) == "active")
    return revived / len(resampled)


def _task_success_for_trace(task_trace: dict) -> float:
    if task_trace.get("task_success") is not None:
        return 1.0 if task_trace.get("task_success") else 0.0
    if task_trace.get("outcome") is not None:
        return 1.0 if task_trace.get("outcome") == "success" else 0.0
    final_state = task_trace.get("final_device_state", {})
    ground_truth_state = task_trace.get("ground_truth_state", {})
    return 1.0 if final_state == ground_truth_state and final_state else 0.0


def task_metrics(task_trace: dict) -> dict:
    final_state = task_trace.get("final_device_state", {})
    ground_truth_state = task_trace.get("ground_truth_state", {})
    tsr = _task_success_for_trace(task_trace)

    usable_steps = task_trace.get("steps", [])
    stale_hits = 0
    total_steps = 0
    for step in usable_steps:
        total_steps += 1
        if any(
            item.get("true_status") in {"expired", "superseded"}
            and item.get("in_usable_set")
            for item in step.get("retrieved_memories", [])
        ):
            stale_hits += 1
    srr = stale_hits / total_steps if total_steps else 0.0

    chosen = (task_trace.get("chosen_action") or {}).get("entity_id")
    gt_entity = task_trace.get("ground_truth_entity")
    task_type = task_trace.get("task_type", "control")
    if task_type not in {"control", "safety", "automation"} or gt_entity is None:
        wdr = 0.0
    else:
        wdr = 0.0 if chosen == gt_entity else 1.0
    cb = float(task_trace.get("clarification_turns", 0))
    pm = None
    if task_trace.get("preferred_action") is not None:
        pm = 1.0 if task_trace.get("chosen_action") == task_trace.get("preferred_action") else 0.0
    uaa = None
    if task_trace.get("safety_relevant"):
        uaa = 1.0 if task_trace.get("safety_gated") else 0.0
    uc = _uc_for_trace(task_trace)
    mp = _mp_for_trace(task_trace)
    dmr = _dmr_for_trace(task_trace)
    rrr = _rrr_for_trace(task_trace)
    return {
        "TSR": tsr,
        "task_success": 1.0 if task_trace.get("task_success") else (0.0 if task_trace.get("task_success") is not None else None),
        "action_success": 1.0 if task_trace.get("action_success") else (0.0 if task_trace.get("action_success") is not None else None),
        "clarification_success": 1.0 if task_trace.get("clarification_success") else (0.0 if task_trace.get("clarification_success") is not None else None),
        "memory_assertion_success": 1.0 if task_trace.get("memory_assertion_success") else (0.0 if task_trace.get("memory_assertion_success") is not None else None),
        "final_state_success": 1.0 if task_trace.get("final_state_success") else (0.0 if task_trace.get("final_state_success") is not None else None),
        "SRR": srr,
        "WDR": wdr,
        "CB": cb,
        "PM": pm,
        "UAA": uaa,
        "UC": uc,
        "MP": mp,
        "DMR": dmr,
        "RRR": rrr,
        "prompt_tokens": float(task_trace.get("prompt_tokens", 0)),
        "end_to_end_latency_ms": float(task_trace.get("end_to_end_latency_ms", 0)),
        "maintenance_latency_ms": float(task_trace.get("maintenance_latency_ms", 0)),
        "maintenance_tokens": float(task_trace.get("maintenance_tokens", 0)),
    }


def aggregate_task_metrics(task_traces: list[dict]) -> dict:
    if not task_traces:
        return {}
    rows = [task_metrics(item) for item in task_traces]
    summary = {}
    for key in rows[0]:
        values = [row[key] for row in rows if row[key] is not None]
        summary[key] = mean(values) if values else 0.0
    prompt_mean = max(summary["prompt_tokens"], 1.0)
    summary["Context Efficiency"] = summary["TSR"] / prompt_mean * 1000
    reference_cb = max(1.0, summary["CB"])
    summary["CE"] = ((reference_cb - summary["CB"]) / reference_cb) * summary["TSR"]
    summary["ECE"] = _ece_rows(task_traces)
    return summary
