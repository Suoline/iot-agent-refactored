from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts._artifact_paths import preferred_run_ids, figures_root, results_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_metric_rows() -> list[dict]:
    rows: list[dict] = []
    root = results_root() / "aggregated_metrics"
    wanted_runs = preferred_run_ids()
    for path in sorted(root.rglob("metrics.summary.json")):
        parts = path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        if run_id not in wanted_runs:
            continue
        payload = _load_json(path)
        for metric, stats in payload.items():
            rows.append(
                {
                    "run_id": run_id,
                    "system_id": system_id,
                    "planner_mode": planner_mode,
                    "metric": metric,
                    "mean": stats.get("mean"),
                    "ci_low": stats.get("ci_low"),
                    "ci_high": stats.get("ci_high"),
                    "count": stats.get("count"),
                }
            )
    for path in sorted(root.rglob("metrics.json")):
        summary_equiv = path.with_name("metrics.summary.json")
        if summary_equiv.exists():
            continue
        parts = path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        if run_id not in wanted_runs:
            continue
        payload = _load_json(path)
        for metric, value in payload.items():
            rows.append(
                {
                    "run_id": run_id,
                    "system_id": system_id,
                    "planner_mode": planner_mode,
                    "metric": metric,
                    "mean": value,
                    "ci_low": value,
                    "ci_high": value,
                    "count": 1,
                }
            )
    return rows


def _load_per_scenario_rows() -> list[dict]:
    rows: list[dict] = []
    root = results_root() / "aggregated_metrics"
    wanted_runs = preferred_run_ids()
    for path in sorted(root.rglob("per_scenario.csv")):
        parts = path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        if run_id not in wanted_runs:
            continue
        with path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append({"run_id": run_id, "system_id": system_id, "planner_mode": planner_mode, **row})
    return rows


def _write_csv(path: Path, rows: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _figure_1(metric_rows: list[dict]) -> list[dict]:
    return [row for row in metric_rows if row["metric"] == "ECE"]


def _figure_2(per_scenario_rows: list[dict]) -> list[dict]:
    return [row for row in per_scenario_rows if row["scenario_id"].startswith(("A", "C", "D", "G"))]


def _figure_3(metric_rows: list[dict]) -> list[dict]:
    return [row for row in metric_rows if row["metric"] in {"DMR", "RRR"}]


def _figure_4(metric_rows: list[dict]) -> list[dict]:
    return [row for row in metric_rows if row["metric"] in {"CB", "TSR"}]


def _figure_5(metric_rows: list[dict]) -> list[dict]:
    return [row for row in metric_rows if row["metric"] == "Estimated Context Efficiency"]


def main():
    metric_rows = _load_metric_rows()
    per_scenario_rows = _load_per_scenario_rows()
    out_dir = figures_root()
    figures = {
        "figure_1.csv": _figure_1(metric_rows),
        "figure_2.csv": _figure_2(per_scenario_rows),
        "figure_3.csv": _figure_3(metric_rows),
        "figure_4.csv": _figure_4(metric_rows),
        "figure_5.csv": _figure_5(metric_rows),
    }
    for filename, rows in figures.items():
        _write_csv(out_dir / filename, rows)
        print(out_dir / filename)


if __name__ == "__main__":
    main()
