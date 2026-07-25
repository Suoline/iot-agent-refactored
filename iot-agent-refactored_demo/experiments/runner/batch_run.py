from __future__ import annotations

import csv
import json
import subprocess
from statistics import mean, pstdev
from pathlib import Path

from experiments.metrics.core import aggregate_task_metrics, task_metrics
from experiments.runner.scenario_loader import load_scenario
from experiments.runner.single_run import run_agent_scenario, run_oracle_scenario
from experiments.runner.system_registry import SystemConfig, build_system_registry
from experiments.trace.writer import TraceWriter


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


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            cwd=Path(__file__).resolve().parents[2],
        ).strip()
    except Exception:
        return "unknown"


def run_batch(
    scenario_paths: list[str | Path],
    *,
    seed: int = 1001,
    results_root: str | Path = "experiments/results",
    system_id: str = "Ours",
    planner_mode: str = "oracle",
    run_id: str = "dev",
):
    registry = build_system_registry()
    system_config = registry.get(system_id, SystemConfig(system_id=system_id))
    writer = TraceWriter(results_root)
    traces = []
    for path in scenario_paths:
        scenario = load_scenario(path)
        if scenario["planner_mode"] == "agent":
            trace = run_agent_scenario(
                scenario,
                seed=seed,
                results_root=results_root,
                system_config=system_config,
            )
        else:
            trace = run_oracle_scenario(
                scenario,
                seed=seed,
                results_root=results_root,
                system_config=system_config,
            )
        trace["system_id"] = system_id
        relative = f"raw_traces/{run_id}/{system_id}/{scenario['planner_mode']}/{scenario['scenario_id']}/{seed}.json"
        writer.write_json(relative, trace)
        writer.write_json(
            f"raw_traces/{run_id}/{system_id}/{scenario['planner_mode']}/{scenario['scenario_id']}/{seed}.maintenance.json",
            {"maintenance_events": trace.get("maintenance_events", [])},
        )
        traces.append(trace)
    metrics = aggregate_task_metrics(traces)
    writer.write_json(f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/metrics.json", metrics)
    per_scenario_path = (
        Path(results_root)
        / "aggregated_metrics"
        / run_id
        / system_id
        / planner_mode
        / "per_scenario.csv"
    )
    per_scenario_path.parent.mkdir(parents=True, exist_ok=True)
    with per_scenario_path.open("w", encoding="utf-8", newline="") as fh:
        writer_csv = csv.writer(fh)
        sample_row = task_metrics(traces[0]) if traces else {}
        metric_fields = list(sample_row.keys())
        writer_csv.writerow(["scenario_id", "seed", *metric_fields])
        for trace in traces:
            row = task_metrics(trace)
            writer_csv.writerow(
                [
                    trace["scenario_id"],
                    trace["seed"],
                    *[row[field] for field in metric_fields],
                ]
            )
    manifest = {
        "run_id": run_id,
        "system_id": system_id,
        "planner_mode": planner_mode,
        "seed": seed,
        "scenario_count": len(traces),
        "git_revision": _git_revision(),
        "world_version": traces[0]["world_version"] if traces else None,
        "system_policy_version": traces[0].get("system_policy_version") if traces else None,
        "trace_files": [
            f"raw_traces/{run_id}/{system_id}/{trace['planner_mode']}/{trace['scenario_id']}/{trace['seed']}.json"
            for trace in traces
        ],
        "maintenance_trace_files": [
            f"raw_traces/{run_id}/{system_id}/{trace['planner_mode']}/{trace['scenario_id']}/{trace['seed']}.maintenance.json"
            for trace in traces
        ],
        "metrics_file": f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/metrics.json",
        "per_scenario_file": f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/per_scenario.csv",
    }
    writer.write_json(f"reports/{run_id}/{system_id}/{planner_mode}/manifest.json", manifest)
    return {"traces": traces, "metrics": metrics}


def run_batch_multi_seed(
    scenario_paths: list[str | Path],
    *,
    seeds: list[int],
    results_root: str | Path = "experiments/results",
    system_id: str = "Ours",
    planner_mode: str = "oracle",
    run_id: str = "dev_multi",
):
    writer = TraceWriter(results_root)
    all_traces = []
    by_seed: dict[int, dict] = {}
    for seed in seeds:
        result = run_batch(
            scenario_paths,
            seed=seed,
            results_root=results_root,
            system_id=system_id,
            planner_mode=planner_mode,
            run_id=run_id,
        )
        all_traces.extend(result["traces"])
        by_seed[seed] = result["metrics"]
    aggregate = aggregate_task_metrics(all_traces)
    writer.write_json(f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/metrics.multi_seed.json", aggregate)
    writer.write_json(
        f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/metrics.by_seed.json",
        by_seed,
    )
    grouped: dict[str, list[dict]] = {}
    for trace in all_traces:
        grouped.setdefault(trace["scenario_id"], []).append(task_metrics(trace))
    summary = {}
    if by_seed:
        metric_names = list(next(iter(by_seed.values())).keys())
        for metric in metric_names:
            values = [metrics[metric] for metrics in by_seed.values()]
            values_sorted = sorted(values)
            summary[metric] = {
                "count": len(values),
                "mean": mean(values),
                "std": pstdev(values) if len(values) > 1 else 0.0,
                "min": min(values),
                "max": max(values),
                "ci_low": _percentile(values_sorted, 0.025),
                "ci_high": _percentile(values_sorted, 0.975),
            }
    per_scenario_summary = []
    for scenario_id, rows in grouped.items():
        row = {"scenario_id": scenario_id, "sample_count": len(rows)}
        metric_names = list(rows[0].keys())
        for metric in metric_names:
            values = [item[metric] for item in rows if item[metric] is not None]
            if not values:
                continue
            values_sorted = sorted(values)
            row[f"{metric}_mean"] = mean(values)
            row[f"{metric}_ci_low"] = _percentile(values_sorted, 0.025)
            row[f"{metric}_ci_high"] = _percentile(values_sorted, 0.975)
        per_scenario_summary.append(row)
    writer.write_json(
        f"aggregated_metrics/{run_id}/{system_id}/{planner_mode}/metrics.summary.json",
        summary,
    )
    per_scenario_multi_path = (
        Path(results_root)
        / "aggregated_metrics"
        / run_id
        / system_id
        / planner_mode
        / "per_scenario.multi_seed.csv"
    )
    per_scenario_multi_path.parent.mkdir(parents=True, exist_ok=True)
    if per_scenario_summary:
        with per_scenario_multi_path.open("w", encoding="utf-8", newline="") as fh:
            fieldnames = list(per_scenario_summary[0].keys())
            writer_csv = csv.DictWriter(fh, fieldnames=fieldnames)
            writer_csv.writeheader()
            for row in per_scenario_summary:
                writer_csv.writerow(row)
    return {
        "traces": all_traces,
        "metrics": aggregate,
        "by_seed": by_seed,
        "summary": summary,
        "per_scenario_summary": per_scenario_summary,
    }
