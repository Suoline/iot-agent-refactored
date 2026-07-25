from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from math import comb, sqrt
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts._artifact_paths import configured_run_id, result_stage, results_root


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


def _bootstrap_mean_ci(values: list[float], *, samples: int = 1000) -> tuple[float, float, float]:
    if not values:
        return 0.0, 0.0, 0.0
    if len(values) == 1:
        value = values[0]
        return value, value, value
    rng = random.Random(20260724)
    means = []
    for _ in range(samples):
        picked = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(mean(picked))
    means.sort()
    return mean(values), _percentile(means, 0.025), _percentile(means, 0.975)


def _sample_std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return sqrt(sum((value - avg) ** 2 for value in values) / (len(values) - 1))


def _cohen_d(values: list[float]) -> float | None:
    sd = _sample_std(values)
    if sd == 0.0:
        return 0.0 if mean(values) == 0.0 else None
    return mean(values) / sd


def _sign_test_p_value(values: list[float]) -> float:
    non_zero = [value for value in values if value != 0.0]
    count = len(non_zero)
    if count == 0:
        return 1.0
    positive = sum(1 for value in non_zero if value > 0.0)
    lo = min(positive, count - positive)
    tail = sum(comb(count, index) for index in range(lo + 1)) / (2 ** count)
    return min(1.0, 2.0 * tail)


def _holm_adjust(metric_rows: list[tuple[str, float]]) -> dict[str, float]:
    adjusted: dict[str, float] = {}
    ordered = sorted(metric_rows, key=lambda item: (item[1], item[0]))
    total = len(ordered)
    running = 0.0
    for index, (metric, p_value) in enumerate(ordered):
        holm_value = min(1.0, (total - index) * p_value)
        running = max(running, holm_value)
        adjusted[metric] = running
    return adjusted


def _load_by_seed(path: Path) -> dict[str, dict]:
    return _load_json(path)


def _compare_run(run_id: str, ours_run_id: str, system_id: str, planner_mode: str) -> dict | None:
    root = results_root() / "aggregated_metrics"
    filename = "metrics.by_scenario.json" if planner_mode == "oracle" else "metrics.by_seed.json"
    ours_path = root / ours_run_id / "Ours" / planner_mode / filename
    other_path = root / run_id / system_id / planner_mode / filename
    if not ours_path.exists() or not other_path.exists():
        return None
    ours = _load_by_seed(ours_path)
    other = _load_by_seed(other_path)
    common = sorted(set(ours) & set(other))
    if not common:
        return None
    metrics = {}
    ours_metric_names = set()
    for row in ours.values():
        ours_metric_names.update(row.keys())
    other_metric_names = set()
    for row in other.values():
        other_metric_names.update(row.keys())
    metric_names = sorted(ours_metric_names & other_metric_names)
    metric_p_values: list[tuple[str, float]] = []
    metric_payloads: dict[str, dict] = {}
    for metric in metric_names:
        deltas = []
        for unit in common:
            if (
                metric not in ours[unit]
                or metric not in other[unit]
                or ours[unit][metric] is None
                or other[unit][metric] is None
            ):
                continue
            deltas.append(other[unit][metric] - ours[unit][metric])
        if not deltas:
            continue
        mean_delta, ci_low, ci_high = _bootstrap_mean_ci(deltas)
        p_value = _sign_test_p_value(deltas)
        metric_p_values.append((metric, p_value))
        metric_payloads[metric] = {
            "paired_count": len(deltas),
            "delta_mean_vs_ours": mean_delta,
            "delta_ci_low": ci_low,
            "delta_ci_high": ci_high,
            "cohen_d": _cohen_d(deltas),
            "p_value": p_value,
        }
    primary_metrics = {"TSR", "State TSR", "SRR", "WDR", "CE", "UC"}
    primary_rows = [row for row in metric_p_values if row[0] in primary_metrics]
    secondary_rows = [row for row in metric_p_values if row[0] not in primary_metrics]
    holm_adjusted = {**_holm_adjust(primary_rows), **_holm_adjust(secondary_rows)}
    for metric, payload in metric_payloads.items():
        payload["holm_adjusted_p"] = holm_adjusted.get(metric, 1.0)
        payload["metric_family"] = "primary" if metric in primary_metrics else "secondary"
        metrics[metric] = payload
    return {
        "run_id": run_id,
        "system_id": system_id,
        "planner_mode": planner_mode,
        "sampling_unit": "scenario" if planner_mode == "oracle" else "seed",
        "test_method": "paired_exact_sign_test",
        "metrics": metrics,
    }


def main():
    comparisons = []
    stage = result_stage()
    oracle_ours_run_id = configured_run_id("oracle")
    for run_id in [configured_run_id("baseline"), configured_run_id("ablation")]:
        run_root = results_root() / "aggregated_metrics" / run_id
        if not run_root.exists():
            continue
        for system_dir in sorted(run_root.iterdir()):
            system_id = system_dir.name
            if system_id == "Ours":
                continue
            planner_mode = next((child.name for child in system_dir.iterdir() if child.is_dir()), None)
            if not planner_mode:
                continue
            comparison = _compare_run(run_id, oracle_ours_run_id, system_id, planner_mode)
            if comparison:
                comparisons.append(comparison)

    out_dir = results_root() / "reports" / stage
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "significance_summary.json"
    out_path.write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
