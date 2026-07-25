from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_metric_rows() -> list[dict]:
    rows: list[dict] = []
    root = REPO_ROOT / "experiments" / "results" / "aggregated_metrics"
    for summary_path in sorted(root.rglob("metrics.summary.json")):
        parts = summary_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        payload = _load_json(summary_path)
        row = {
            "run_id": run_id,
            "system_id": system_id,
            "planner_mode": planner_mode,
            "source_kind": "summary",
        }
        for metric, stats in payload.items():
            row[f"{metric}_mean"] = stats.get("mean")
            row[f"{metric}_ci_low"] = stats.get("ci_low")
            row[f"{metric}_ci_high"] = stats.get("ci_high")
            row[f"{metric}_count"] = stats.get("count")
        rows.append(row)
    for metrics_path in sorted(root.rglob("metrics.json")):
        parts = metrics_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        summary_equiv = metrics_path.with_name("metrics.summary.json")
        if summary_equiv.exists():
            continue
        payload = _load_json(metrics_path)
        row = {
            "run_id": run_id,
            "system_id": system_id,
            "planner_mode": planner_mode,
            "source_kind": "single",
        }
        for metric, value in payload.items():
            row[f"{metric}_mean"] = value
            row[f"{metric}_ci_low"] = value
            row[f"{metric}_ci_high"] = value
            row[f"{metric}_count"] = 1
        rows.append(row)
    return rows


def _load_per_scenario_rows() -> list[dict]:
    rows: list[dict] = []
    root = REPO_ROOT / "experiments" / "results" / "aggregated_metrics"
    for csv_path in sorted(root.rglob("per_scenario.csv")):
        parts = csv_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        with csv_path.open("r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                rows.append(
                    {
                        "run_id": run_id,
                        "system_id": system_id,
                        "planner_mode": planner_mode,
                        **row,
                    }
                )
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


def _table_1(metric_rows: list[dict]) -> list[dict]:
    wanted = {"B0", "B1", "B2", "B3", "B4", "B5", "Ours"}
    preferred_runs = {"configured_baseline_dev", "configured_agent_dev", "baseline_dev", "dev_agent"}
    return [
        row for row in metric_rows
        if row["system_id"] in wanted and row["run_id"] in preferred_runs
    ]


def _table_2(metric_rows: list[dict]) -> list[dict]:
    wanted = {
        "-Decay",
        "-AsymFeedback",
        "-Governance",
        "-CandidateGate",
        "-ConflictHandling",
        "-FeatureAbsorption",
        "-Ripple",
        "-Split",
        "Ours",
    }
    preferred_runs = {"configured_ablation_dev", "ablation_dev"}
    return [
        row for row in metric_rows
        if row["system_id"] in wanted and row["run_id"] in preferred_runs
    ]


def _table_3(per_scenario_rows: list[dict]) -> list[dict]:
    grouped: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    for row in per_scenario_rows:
        scenario_id = row["scenario_id"]
        category = scenario_id[0]
        key = (row["run_id"], row["system_id"], row["planner_mode"], category)
        grouped[key].append(row)
    table = []
    for (run_id, system_id, planner_mode, category), rows in grouped.items():
        table.append(
            {
                "run_id": run_id,
                "system_id": system_id,
                "planner_mode": planner_mode,
                "category": category,
                "scenario_count": len(rows),
                "TSR_mean": sum(float(r["TSR"]) for r in rows) / len(rows),
                "SRR_mean": sum(float(r["SRR"]) for r in rows) / len(rows),
                "WDR_mean": sum(float(r["WDR"]) for r in rows) / len(rows),
            }
        )
    return sorted(table, key=lambda row: (row["run_id"], row["system_id"], row["category"]))


def _table_4(metric_rows: list[dict]) -> list[dict]:
    return [
        {
            key: row.get(key)
            for key in [
                "run_id",
                "system_id",
                "planner_mode",
                "end_to_end_latency_ms_mean",
                "maintenance_latency_ms_mean",
                "prompt_tokens_mean",
                "Context Efficiency_mean",
            ]
        }
        for row in metric_rows
    ]


def _table_5(metric_rows: list[dict]) -> list[dict]:
    return [
        {
            key: row.get(key)
            for key in [
                "run_id",
                "system_id",
                "planner_mode",
                "CB_mean",
                "UAA_mean",
                "PM_mean",
            ]
        }
        for row in metric_rows
    ]


def main():
    metric_rows = _load_metric_rows()
    per_scenario_rows = _load_per_scenario_rows()
    out_dir = REPO_ROOT / "experiments" / "results" / "tables" / "dev"
    tables = {
        "table_1.csv": _table_1(metric_rows),
        "table_2.csv": _table_2(metric_rows),
        "table_3.csv": _table_3(per_scenario_rows),
        "table_4.csv": _table_4(metric_rows),
        "table_5.csv": _table_5(metric_rows),
    }
    for filename, rows in tables.items():
        _write_csv(out_dir / filename, rows)
        print(out_dir / filename)


if __name__ == "__main__":
    main()
