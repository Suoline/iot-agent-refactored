from __future__ import annotations

import csv
import json
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, pstdev

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ablations import ABLATION_IDS
from experiments.baselines import BASELINE_IDS
from experiments.metrics.core import aggregate_task_metrics, task_metrics


RESULTS_ROOT = REPO_ROOT / "experiments" / "results" / "agent_llm_smoke"
FORMAL_V2_ROOT = REPO_ROOT / "experiments" / "results" / "formal_v2"
RUN_ID = "real_llm_candidate_20260725_two_seed"
SYSTEM_ID = "Ours"
PLANNER_MODE = "agent"

TRACE_SPECS = [
    {"scenario_id": "G1", "seed": 1001, "run_id": "g1_seed1001_real_llm"},
    {"scenario_id": "E1", "seed": 1001, "run_id": "e1_seed1001_real_llm"},
    {"scenario_id": "B6", "seed": 1001, "run_id": "b6_seed1001_real_llm"},
    {"scenario_id": "E2", "seed": 1001, "run_id": "e2_seed1001_real_llm"},
    {"scenario_id": "E3", "seed": 1001, "run_id": "e3_seed1001_real_llm"},
    {"scenario_id": "G1", "seed": 1002, "run_id": "g1_seed1002_real_llm"},
    {"scenario_id": "E1", "seed": 1002, "run_id": "e1_seed1002_real_llm"},
    {"scenario_id": "B6", "seed": 1002, "run_id": "b6_seed1002_real_llm_retry1"},
    {"scenario_id": "E2", "seed": 1002, "run_id": "e2_seed1002_real_llm"},
    {"scenario_id": "E3", "seed": 1002, "run_id": "e3_seed1002_real_llm"},
]

FAILED_ATTEMPTS = [
    {"scenario_id": "B6", "seed": 1002, "run_id": "b6_seed1002_real_llm"},
]


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            cwd=REPO_ROOT,
        ).strip()
    except Exception:
        return "unknown"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _percentile(sorted_values: list[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    idx = (len(sorted_values) - 1) * q
    lower = int(idx)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = idx - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _bootstrap_mean_ci(values: list[float], *, samples: int = 2000) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    if len(values) == 1 or len(set(values)) == 1:
        value = mean(values)
        return value, value
    rng = __import__("random").Random(20260725)
    means = [
        mean(values[rng.randrange(len(values))] for _ in values)
        for _ in range(samples)
    ]
    means.sort()
    return _percentile(means, 0.025), _percentile(means, 0.975)


def _sum_agent_usage(traces: list[dict]) -> dict[str, int]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }
    for trace in traces:
        for batch in trace.get("agent_usage_metadata", []):
            for key in totals:
                value = batch.get(key)
                if isinstance(value, (int, float)):
                    totals[key] += int(value)
    return totals


def _mean_or_none(values: list[float]) -> float | None:
    return mean(values) if values else None


def _trace_path(run_id: str, scenario_id: str, seed: int) -> Path:
    return RESULTS_ROOT / "raw_traces" / run_id / SYSTEM_ID / PLANNER_MODE / scenario_id / f"{seed}.json"


def _manifest_path(run_id: str) -> Path:
    return RESULTS_ROOT / "reports" / run_id / SYSTEM_ID / PLANNER_MODE / "manifest.json"


def _load_selected_traces() -> tuple[list[dict], list[dict], list[dict]]:
    traces: list[dict] = []
    source_manifests: list[dict] = []
    failed_attempts: list[dict] = []
    for spec in TRACE_SPECS:
        trace_path = _trace_path(spec["run_id"], spec["scenario_id"], spec["seed"])
        manifest_path = _manifest_path(spec["run_id"])
        trace = _load_json(trace_path)
        trace["_source_run_id"] = spec["run_id"]
        trace["_source_trace_file"] = str(trace_path.relative_to(RESULTS_ROOT))
        traces.append(trace)
        source_manifests.append(_load_json(manifest_path))
    for spec in FAILED_ATTEMPTS:
        trace_path = _trace_path(spec["run_id"], spec["scenario_id"], spec["seed"])
        manifest_path = _manifest_path(spec["run_id"])
        trace = _load_json(trace_path)
        failed_attempts.append(
            {
                "scenario_id": spec["scenario_id"],
                "seed": spec["seed"],
                "run_id": spec["run_id"],
                "trace_file": str(trace_path.relative_to(RESULTS_ROOT)),
                "manifest_file": str(manifest_path.relative_to(RESULTS_ROOT)),
                "outcome": trace.get("outcome"),
                "task_success": trace.get("task_success"),
                "agent_backend": trace.get("agent_backend"),
                "agent_failures": trace.get("agent_failures", []),
                "clarification_turns": trace.get("clarification_turns"),
            }
        )
    return traces, source_manifests, failed_attempts


def _build_summary(traces: list[dict]) -> tuple[dict[int, dict], dict[str, dict], dict[str, dict], list[dict]]:
    grouped_by_seed: dict[int, list[dict]] = {}
    grouped_by_scenario: dict[str, list[dict]] = {}
    for trace in traces:
        grouped_by_seed.setdefault(trace["seed"], []).append(trace)
        grouped_by_scenario.setdefault(trace["scenario_id"], []).append(trace)

    by_seed = {
        seed: aggregate_task_metrics(seed_traces)
        for seed, seed_traces in grouped_by_seed.items()
    }
    by_scenario = {
        scenario_id: aggregate_task_metrics(scenario_traces)
        for scenario_id, scenario_traces in grouped_by_scenario.items()
    }

    summary: dict[str, dict] = {}
    metric_names = sorted({key for row in by_seed.values() for key in row})
    for metric in metric_names:
        values = [row[metric] for row in by_seed.values() if row.get(metric) is not None]
        if not values:
            continue
        ci_low, ci_high = _bootstrap_mean_ci(values)
        summary[metric] = {
            "count": len(values),
            "mean": mean(values),
            "std": pstdev(values) if len(values) > 1 else 0.0,
            "min": min(values),
            "max": max(values),
            "ci_low": ci_low,
            "ci_high": ci_high,
            "sampling_unit": "seed",
        }
    for metric in {"SRR", "MP", "DMR", "RRR"}:
        aggregate = aggregate_task_metrics(traces)
        if metric in summary and aggregate.get(metric) is not None:
            summary[metric]["mean"] = aggregate[metric]
            summary[metric]["aggregation"] = "pooled_numerator_denominator"

    per_scenario_summary = []
    for scenario_id, scenario_traces in grouped_by_scenario.items():
        rows = [task_metrics(item) for item in scenario_traces]
        row = {"scenario_id": scenario_id, "sample_count": len(rows)}
        metric_fields = list(rows[0].keys())
        for metric in metric_fields:
            values = [item[metric] for item in rows if item[metric] is not None]
            if not values:
                continue
            values_sorted = sorted(values)
            row[f"{metric}_mean"] = mean(values)
            row[f"{metric}_ci_low"] = _percentile(values_sorted, 0.025)
            row[f"{metric}_ci_high"] = _percentile(values_sorted, 0.975)
        per_scenario_summary.append(row)
    per_scenario_summary.sort(key=lambda item: item["scenario_id"])
    return by_seed, by_scenario, summary, per_scenario_summary


def _formal_summary(path: Path) -> dict[str, float | None]:
    payload = _load_json(path)
    metrics = {}
    for key in [
        "TSR",
        "State TSR",
        "WDR",
        "CB",
        "PM",
        "UAA",
        "MP",
        "DMR",
        "RRR",
        "Estimated Prompt Tokens",
        "end_to_end_latency_ms",
    ]:
        value = payload.get(key)
        metrics[key] = value.get("mean") if isinstance(value, dict) else None
    return metrics


def _build_comparison(candidate_summary: dict[str, dict]) -> dict:
    candidate_means = {key: value.get("mean") for key, value in candidate_summary.items()}
    formal = {
        "oracle_ours": _formal_summary(
            FORMAL_V2_ROOT / "aggregated_metrics" / "configured_oracle_formal_v2" / "Ours" / "oracle" / "metrics.summary.json"
        ),
        "heuristic_agent_ours": _formal_summary(
            FORMAL_V2_ROOT / "aggregated_metrics" / "configured_agent_formal_v2" / "Ours" / "agent" / "metrics.summary.json"
        ),
        "baselines": {},
        "ablations": {},
    }
    for baseline_id in BASELINE_IDS:
        path = FORMAL_V2_ROOT / "aggregated_metrics" / "configured_baseline_formal_v2" / baseline_id / "oracle" / "metrics.summary.json"
        if path.exists():
            formal["baselines"][baseline_id] = _formal_summary(path)
    for ablation_id in ABLATION_IDS:
        path = FORMAL_V2_ROOT / "aggregated_metrics" / "configured_ablation_formal_v2" / ablation_id / "oracle" / "metrics.summary.json"
        if path.exists():
            formal["ablations"][ablation_id] = _formal_summary(path)
    return {
        "candidate_run_id": RUN_ID,
        "candidate_sampling_unit": "seed",
        "directly_comparable_formal_run": "configured_agent_formal_v2",
        "notes": [
            "只有 heuristic Agent formal_v2 与该 candidate 共享相同的 5 个 agent 场景集合和 seed 级采样单位。",
            "Oracle、baseline、ablation formal_v2 使用 31 个 oracle 场景，指标字段同口径但不构成严格 apples-to-apples 比较。",
        ],
        "candidate_means": candidate_means,
        "formal_context": formal,
        "candidate_minus_heuristic_agent": {
            key: candidate_means.get(key) - value
            for key, value in formal["heuristic_agent_ours"].items()
            if candidate_means.get(key) is not None and value is not None
        },
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_per_scenario_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fieldnames: list[str] = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main() -> None:
    traces, source_manifests, failed_attempts = _load_selected_traces()
    aggregate = aggregate_task_metrics(traces)
    by_seed, by_scenario, summary, per_scenario_summary = _build_summary(traces)
    comparison = _build_comparison(summary)

    scenario_ids = sorted({trace["scenario_id"] for trace in traces})
    seeds = sorted({trace["seed"] for trace in traces})
    usage_totals = _sum_agent_usage(traces)
    backends = sorted({trace.get("agent_backend") for trace in traces if trace.get("agent_backend")})
    providers = sorted({trace.get("agent_provider") for trace in traces if trace.get("agent_provider")})
    models = sorted({trace.get("agent_model") for trace in traces if trace.get("agent_model")})
    failure_counter = Counter()
    for trace in traces:
        for failure in trace.get("agent_failures", []):
            failure_counter[failure] += 1
    latency_values = [
        value
        for trace in traces
        for value in trace.get("agent_latencies_ms", [])
        if isinstance(value, (int, float))
    ]
    clarification_count = sum(1 for trace in traces if trace.get("clarification_turns", 0) > 0)
    action_count = sum(1 for trace in traces if trace.get("action_execution_results"))
    source_revision_map = {
        manifest.get("run_id"): manifest.get("git_revision")
        for manifest in source_manifests
    }

    metrics_root = RESULTS_ROOT / "aggregated_metrics" / RUN_ID / SYSTEM_ID / PLANNER_MODE
    reports_root = RESULTS_ROOT / "reports" / RUN_ID / SYSTEM_ID / PLANNER_MODE
    _write_json(metrics_root / "metrics.multi_seed.json", aggregate)
    _write_json(metrics_root / "metrics.by_seed.json", by_seed)
    _write_json(metrics_root / "metrics.by_scenario.json", by_scenario)
    _write_json(metrics_root / "metrics.summary.json", summary)
    _write_per_scenario_csv(metrics_root / "per_scenario.multi_seed.csv", per_scenario_summary)

    audit = {
        "run_id": RUN_ID,
        "status": "pass",
        "task_count": len(traces),
        "scenario_count": len(scenario_ids),
        "seed_count": len(seeds),
        "task_success_count": sum(1 for trace in traces if trace.get("task_success") is True),
        "all_backends_external_llm": backends == ["external_llm"],
        "backend_values": backends,
        "provider_values": providers,
        "model_values": models,
        "no_heuristic_fallback": "heuristic_fallback" not in backends,
        "agent_api_call_count": sum(len(trace.get("agent_usage_metadata", [])) for trace in traces),
        "agent_usage_totals": usage_totals,
        "latency_summary_ms": {
            "count": len(latency_values),
            "mean": _mean_or_none(latency_values),
            "min": min(latency_values) if latency_values else None,
            "max": max(latency_values) if latency_values else None,
        },
        "clarification_trace_count": clarification_count,
        "action_trace_count": action_count,
        "failure_type_counts": dict(failure_counter),
        "failed_attempts": failed_attempts,
        "source_run_git_revisions": source_revision_map,
    }
    _write_json(reports_root / "audit.json", audit)
    _write_json(reports_root / "comparison.json", comparison)

    manifest = {
        "run_id": RUN_ID,
        "system_id": SYSTEM_ID,
        "planner_mode": PLANNER_MODE,
        "seeds": seeds,
        "seed_count": len(seeds),
        "scenario_ids": scenario_ids,
        "scenario_count": len(scenario_ids),
        "task_count": len(traces),
        "sampling_unit": "seed",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(),
        "source_run_git_revisions": source_revision_map,
        "agent_backends": backends,
        "agent_models": models,
        "agent_providers": providers,
        "agent_api_call_count": audit["agent_api_call_count"],
        "agent_usage_totals": usage_totals,
        "result_classification": "real_llm_candidate",
        "confirmatory_ready": False,
        "confirmatory_gaps": [
            "真实 LLM Agent 仅覆盖 5 个 agent 场景，尚未达到论文计划中的 secondary N=20。",
            "当前 two-seed candidate 的来源 run 包含 July 25, 2026 之前的单场景 candidate 与 July 25, 2026 当天新增第二 seed，仍属于扩量中的候选结果。",
            "Oracle、baseline、ablation 的 formal_v2 是 31 个 oracle 场景，不能直接替代这 5 个真实 agent 场景结果。",
        ],
        "source_manifest_files": [
            str((_manifest_path(spec["run_id"])).relative_to(RESULTS_ROOT))
            for spec in TRACE_SPECS
        ],
        "trace_files": [trace["_source_trace_file"] for trace in traces],
        "failed_task_ids": [trace["task_id"] for trace in traces if trace.get("outcome") != "success"],
        "failed_attempts": failed_attempts,
        "metrics_by_seed_file": str((metrics_root / "metrics.by_seed.json").relative_to(RESULTS_ROOT)),
        "metrics_by_scenario_file": str((metrics_root / "metrics.by_scenario.json").relative_to(RESULTS_ROOT)),
        "metrics_summary_file": str((metrics_root / "metrics.summary.json").relative_to(RESULTS_ROOT)),
        "per_scenario_file": str((metrics_root / "per_scenario.multi_seed.csv").relative_to(RESULTS_ROOT)),
        "audit_file": str((reports_root / "audit.json").relative_to(RESULTS_ROOT)),
        "comparison_file": str((reports_root / "comparison.json").relative_to(RESULTS_ROOT)),
    }
    _write_json(reports_root / "manifest.json", manifest)
    print(reports_root / "manifest.json")


if __name__ == "__main__":
    main()
