from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from statistics import mean

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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


def _load_by_seed(path: Path) -> dict[str, dict]:
    return _load_json(path)


def _compare_run(run_id: str, system_id: str, planner_mode: str) -> dict | None:
    root = REPO_ROOT / "experiments" / "results" / "aggregated_metrics" / run_id
    ours_path = root / "Ours" / planner_mode / "metrics.by_seed.json"
    other_path = root / system_id / planner_mode / "metrics.by_seed.json"
    if not ours_path.exists() or not other_path.exists():
        return None
    ours = _load_by_seed(ours_path)
    other = _load_by_seed(other_path)
    common = sorted(set(ours) & set(other))
    if not common:
        return None
    metrics = {}
    metric_names = list(next(iter(other.values())).keys())
    for metric in metric_names:
        deltas = [other[seed][metric] - ours[seed][metric] for seed in common]
        mean_delta, ci_low, ci_high = _bootstrap_mean_ci(deltas)
        metrics[metric] = {
            "paired_count": len(common),
            "delta_mean_vs_ours": mean_delta,
            "delta_ci_low": ci_low,
            "delta_ci_high": ci_high,
        }
    return {
        "run_id": run_id,
        "system_id": system_id,
        "planner_mode": planner_mode,
        "metrics": metrics,
    }


def main():
    comparisons = []
    for run_id in ["configured_baseline_dev", "configured_ablation_dev"]:
        run_root = REPO_ROOT / "experiments" / "results" / "aggregated_metrics" / run_id
        if not run_root.exists():
            continue
        for system_dir in sorted(run_root.iterdir()):
            system_id = system_dir.name
            if system_id == "Ours":
                continue
            planner_mode = next((child.name for child in system_dir.iterdir() if child.is_dir()), None)
            if not planner_mode:
                continue
            comparison = _compare_run(run_id, system_id, planner_mode)
            if comparison:
                comparisons.append(comparison)

    out_dir = REPO_ROOT / "experiments" / "results" / "reports" / "dev"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "significance_summary.json"
    out_path.write_text(json.dumps(comparisons, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
