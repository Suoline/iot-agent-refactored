from __future__ import annotations

import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.metrics.core import task_metrics


METRIC_SPECS = {
    "TSR": {"kind": "binary", "direction": "higher"},
    "Control Final-State TSR": {"kind": "binary", "direction": "higher"},
    "Query Answer Accuracy": {"kind": "binary", "direction": "higher"},
    "Automation Decision Accuracy": {"kind": "binary", "direction": "higher"},
    "WDR": {"kind": "binary", "direction": "lower"},
    "Unsafe Action Rate": {"kind": "binary", "direction": "lower"},
    "Necessary Clarification Rate": {"kind": "binary", "direction": "higher"},
    "Unnecessary Clarification Rate": {"kind": "binary", "direction": "lower"},
    "Prompt Tokens": {"kind": "continuous", "direction": "lower"},
    "Completion Tokens": {"kind": "continuous", "direction": "lower"},
    "end_to_end_latency_ms": {"kind": "continuous", "direction": "lower"},
}


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * quantile
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def two_way_cluster_bootstrap(
    paired_rows: list[dict], *, samples: int = 2000, rng_seed: int = 20260814
) -> tuple[float | None, float | None, float | None]:
    """Pigeonhole bootstrap over both scenario and replicate clusters."""
    if not paired_rows:
        return None, None, None
    scenarios = sorted({row["scenario_id"] for row in paired_rows})
    replicates = sorted({row["replicate_id"] for row in paired_rows})
    cells: dict[tuple[str, int], list[float]] = defaultdict(list)
    for row in paired_rows:
        cells[(row["scenario_id"], row["replicate_id"])].append(float(row["delta"]))
    point = _mean([float(row["delta"]) for row in paired_rows])
    rng = random.Random(rng_seed)
    draws: list[float] = []
    for _ in range(samples):
        sampled_scenarios = [scenarios[rng.randrange(len(scenarios))] for _ in scenarios]
        sampled_replicates = [replicates[rng.randrange(len(replicates))] for _ in replicates]
        values = [value for scenario in sampled_scenarios for replicate in sampled_replicates for value in cells.get((scenario, replicate), [])]
        if values:
            draws.append(float(_mean(values)))
    return point, _percentile(draws, 0.025), _percentile(draws, 0.975)


def mcnemar_exact(paired_rows: list[dict]) -> dict:
    table = {"both_success": 0, "ours_only_success": 0, "baseline_only_success": 0, "both_failure": 0}
    for row in paired_rows:
        ours = int(float(row["ours"]) >= 0.5)
        baseline = int(float(row["baseline"]) >= 0.5)
        if ours and baseline:
            table["both_success"] += 1
        elif ours:
            table["ours_only_success"] += 1
        elif baseline:
            table["baseline_only_success"] += 1
        else:
            table["both_failure"] += 1
    discordant = table["ours_only_success"] + table["baseline_only_success"]
    if not discordant:
        p_value = 1.0
    else:
        smaller = min(table["ours_only_success"], table["baseline_only_success"])
        p_value = min(1.0, 2.0 * sum(math.comb(discordant, index) for index in range(smaller + 1)) / (2 ** discordant))
    return {**table, "discordant_count": discordant, "exact_two_sided_p": p_value, "clustered_gee_used": False}


def paired_sign_flip_p_value(paired_rows: list[dict], *, samples: int = 10000, rng_seed: int = 20260815) -> float | None:
    """Two-sided paired randomization test used for continuous Holm entries."""
    deltas = [float(row["delta"]) for row in paired_rows]
    if not deltas:
        return None
    observed = abs(float(_mean(deltas)))
    if observed == 0.0:
        return 1.0
    rng = random.Random(rng_seed)
    extreme = 0
    for _ in range(samples):
        draw = abs(float(_mean([delta if rng.random() < 0.5 else -delta for delta in deltas])))
        extreme += draw >= observed
    return (extreme + 1) / (samples + 1)


def holm_adjust(rows: list[dict]) -> None:
    eligible = [row for row in rows if row.get("raw_p_value") is not None]
    ordered = sorted(eligible, key=lambda row: row["raw_p_value"])
    running = 0.0
    family_size = len(ordered)
    for index, row in enumerate(ordered):
        running = max(running, min(1.0, (family_size - index) * row["raw_p_value"]))
        row["holm_adjusted_p"] = running
        row["holm_family_size"] = family_size


def _formal_behavioral_coverage(by_key: dict) -> dict:
    matrix_path = REPO_ROOT / "experiments/configs/protocol_v4_formal_agent_matrix.json"
    if not matrix_path.exists():
        return {"complete": False, "reason": "frozen_behavioral_matrix_missing"}
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    expected = {(unit["system_id"], unit["scenario_id"], unit["seed"]) for unit in matrix.get("units", [])}
    observed = set(by_key)
    missing = expected - observed
    unexpected = observed - expected
    return {
        "complete": not missing and not unexpected,
        "expected_units": len(expected),
        "observed_units": len(observed),
        "missing_units": len(missing),
        "unexpected_units": len(unexpected),
    }


def _frozen_behavioral_pairs() -> set[tuple[str, int]]:
    matrix_path = REPO_ROOT / "experiments/configs/protocol_v4_formal_agent_matrix.json"
    if not matrix_path.exists():
        return set()
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    return {(unit["scenario_id"], unit["seed"]) for unit in matrix.get("units", [])}


def _plan_outcome(plan: dict | None, expected: dict) -> bool | None:
    if plan is None:
        return None
    actions = plan.get("actions") or []
    should_ask = bool(plan.get("should_ask_user"))
    kind = expected.get("kind")
    if kind == "clarification":
        return should_ask and not actions
    if kind == "no_action":
        return not should_ask and not actions
    if kind in {"action", "query"}:
        target = expected.get("action") or {}
        return len(actions) == 1 and not should_ask and actions[0].get("service") == target.get("service") and actions[0].get("entity_id") == target.get("entity_id") and actions[0].get("args", {}) == target.get("args", {})
    return None


def _plan_semantics(plan: dict | None) -> tuple:
    if plan is None:
        return (None, None)
    actions = tuple(
        (item.get("service"), item.get("entity_id"), json.dumps(item.get("args", {}), sort_keys=True, ensure_ascii=False))
        for item in plan.get("actions", [])
    )
    return actions, bool(plan.get("should_ask_user"))


def _expected_plan_by_step(trace: dict) -> dict[str, dict]:
    decisions = trace.get("raw_planner_decisions", [])
    primary_assertions = [
        item for item in trace.get("assertion_results", [])
        if item.get("kind") in {"action", "query", "clarification"}
    ]
    expected: dict[str, dict] = {}
    for decision, assertion in zip(decisions, primary_assertions):
        step_id = decision.get("step_id")
        if assertion.get("kind") == "clarification":
            expected[step_id] = {"kind": "clarification"}
        elif assertion.get("expected"):
            expected[step_id] = {"kind": assertion.get("kind"), "action": assertion["expected"]}
        else:
            expected[step_id] = {"kind": "no_action"}
    return expected


def guard_diagnostics(traces: list[dict]) -> dict:
    counts = Counter()
    rows = []
    for trace in traces:
        raw_by_step = {item.get("step_id"): item for item in trace.get("raw_planner_decisions", [])}
        guarded_by_step = {item.get("step_id"): item for item in trace.get("guarded_planner_decisions", [])}
        expected_by_step = _expected_plan_by_step(trace)
        for step_id in sorted(set(raw_by_step) | set(guarded_by_step)):
            raw_ok = _plan_outcome(raw_by_step.get(step_id), expected_by_step.get(step_id, {}))
            guarded_ok = _plan_outcome(guarded_by_step.get(step_id), expected_by_step.get(step_id, {}))
            counts["decision_count"] += 1
            if raw_ok is True:
                counts["raw_correct"] += 1
            if guarded_ok is True:
                counts["guarded_correct"] += 1
            if _plan_semantics(raw_by_step.get(step_id)) != _plan_semantics(guarded_by_step.get(step_id)):
                counts["override_count"] += 1
                if raw_ok is False and guarded_ok is True:
                    classification = "corrected"
                elif raw_ok is True and guarded_ok is False:
                    classification = "harmful"
                elif raw_ok is not None and guarded_ok is not None:
                    classification = "neutral"
                else:
                    classification = "unresolved"
                counts[classification] += 1
                rows.append({"system_id": trace.get("system_id"), "scenario_id": trace.get("scenario_id"), "replicate_id": trace.get("seed"), "step_id": step_id, "raw_correct": raw_ok, "guarded_correct": guarded_ok, "classification": classification})
    total = counts["decision_count"]
    return {
        "decision_count": total,
        "raw_planner_accuracy": counts["raw_correct"] / total if total else None,
        "guarded_planner_accuracy": counts["guarded_correct"] / total if total else None,
        "override_count": counts["override_count"],
        "override_rate": counts["override_count"] / total if total else None,
        "override_classification": {key: counts[key] for key in ("corrected", "harmful", "neutral", "unresolved")},
        "override_rows": rows,
    }


def collect_traces(root: Path) -> tuple[dict, dict]:
    by_key = {}
    exclusions = Counter()
    rows = []
    for path in root.glob("raw_traces/**/*.json"):
        if path.name.endswith(".maintenance.json"):
            continue
        trace = json.loads(path.read_text(encoding="utf-8"))
        reason = None
        if trace.get("agent_backend") in {"heuristic", "heuristic_fallback"} or any(
            event.get("kind") == "fallback" for event in trace.get("usage_events", [])
        ):
            reason = "fallback_present"
        elif trace.get("evaluation_protocol") != "v4" or trace.get("agent_backend") != "external_llm":
            reason = "not_valid_external_v4"
        elif any(
            str(item).startswith(("external_call_failed:", "external_init_failed:"))
            for item in trace.get("agent_failures", [])
        ):
            reason = "transport_failure"
        elif trace.get("agent_seed_protocol") == "no_agent_call_required":
            reason = "no_agent_call_required"
        elif trace.get("agent_seed_protocol") not in {"replicate_id", "provider_seed"}:
            reason = "seed_protocol_invalid"
        elif not trace.get("agent_usage_metadata"):
            reason = "real_usage_missing"
        if reason:
            exclusions[reason] += 1
            rows.append({"trace": str(path), "reason": reason})
            continue
        key = (trace.get("system_id"), trace.get("scenario_id"), trace.get("seed"))
        by_key[key] = (trace, task_metrics(trace))
    model_behavior_failures = Counter(
        failure
        for trace, _ in by_key.values()
        for failure in trace.get("agent_failures", [])
        if not str(failure).startswith(("external_call_failed:", "external_init_failed:"))
    )
    return by_key, {
        "counts": dict(exclusions),
        "rows": rows,
        "included_model_behavior_failure_counts": dict(model_behavior_failures),
    }


def analyze(root: Path, *, samples: int = 2000) -> dict:
    by_key, exclusions = collect_traces(root)
    systems = ["B0", "B1", "B2", "B3", "B4", "B5"]
    comparisons = []
    holm_rows = []
    expected_pairs = _frozen_behavioral_pairs()
    for system in systems:
        system_rows = []
        for metric, spec in METRIC_SPECS.items():
            paired = []
            missing_ours = missing_baseline = inapplicable = 0
            for scenario, replicate in sorted(expected_pairs):
                ours = by_key.get(("Ours", scenario, replicate))
                baseline = by_key.get((system, scenario, replicate))
                if not ours:
                    missing_ours += 1
                    continue
                if not baseline:
                    missing_baseline += 1
                    continue
                ours_value = ours[1].get(metric)
                baseline_value = baseline[1].get(metric)
                if ours_value is None or baseline_value is None:
                    inapplicable += 1
                    continue
                paired.append({"scenario_id": scenario, "replicate_id": replicate, "ours": float(ours_value), "baseline": float(baseline_value), "delta": float(ours_value) - float(baseline_value)})
            point, low, high = two_way_cluster_bootstrap(paired, samples=samples)
            direction_multiplier = 1.0 if spec["direction"] == "higher" else -1.0
            mcnemar_rows = paired
            if spec["kind"] == "binary" and spec["direction"] == "lower":
                mcnemar_rows = [
                    {**row, "ours": 1.0 - row["ours"], "baseline": 1.0 - row["baseline"]}
                    for row in paired
                ]
            mcnemar = mcnemar_exact(mcnemar_rows) if spec["kind"] == "binary" else None
            raw_p_value = mcnemar["exact_two_sided_p"] if mcnemar else paired_sign_flip_p_value(paired)
            row = {
                "metric": metric,
                "kind": spec["kind"],
                "preferred_direction": spec["direction"],
                "paired_eligible_count": len(paired),
                "missing_ours_count": missing_ours,
                "missing_baseline_count": missing_baseline,
                "inapplicable_pair_count": inapplicable,
                "ours_minus_baseline": point,
                "ci_low": low,
                "ci_high": high,
                "direction_normalized_effect": point * direction_multiplier if point is not None else None,
                "direction_interpretation": "positive_favors_ours",
                "mcnemar": mcnemar,
                "mcnemar_encoding": "preferred_outcome_success",
                "continuous_sensitivity": (
                    {"method": "paired_sign_flip_randomization", "samples": 10000, "rng_seed": 20260815}
                    if spec["kind"] == "continuous" else None
                ),
                "raw_p_value": raw_p_value,
                "holm_adjusted_p": None,
                "holm_family_size": None,
            }
            system_rows.append(row)
            if row["raw_p_value"] is not None:
                holm_rows.append(row)
        comparisons.append({"system_id": system, "metrics": system_rows})
    holm_adjust(holm_rows)
    traces = [trace for trace, _ in by_key.values()]
    coverage = _formal_behavioral_coverage(by_key)
    return {
        "protocol": "v4",
        "sampling_unit": "scenario_x_replicate_id_paired",
        "bootstrap": {"method": "two_way_pigeonhole_cluster_bootstrap", "clusters": ["scenario_id", "replicate_id"], "samples": samples, "rng_seed": 20260814},
        "seed_interpretation": "replicate_id_not_provider_seed",
        "valid_external_units": len(by_key),
        "exclusions": exclusions,
        "comparisons": comparisons,
        "holm_family": "Ours_vs_B0-B5_x_all_preregistered_primary_metrics",
        "guard_diagnostics": guard_diagnostics(traces),
        "frozen_behavioral_coverage": coverage,
        "status": "formal_analysis_ready" if coverage["complete"] else "descriptive_only",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze protocol-v4 paired external traces.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=2000)
    args = parser.parse_args()
    report = analyze(args.root, samples=args.samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
