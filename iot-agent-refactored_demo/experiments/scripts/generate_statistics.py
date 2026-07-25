from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts._artifact_paths import configured_run_id, result_stage, results_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_summary(path: Path) -> dict:
    payload = _load_json(path)
    row = {
        "run_id": path.parts[-4],
        "system_id": path.parts[-3],
        "planner_mode": path.parts[-2],
    }
    for metric, stats in payload.items():
        row[f"{metric}_mean"] = stats.get("mean")
        row[f"{metric}_ci_low"] = stats.get("ci_low")
        row[f"{metric}_ci_high"] = stats.get("ci_high")
        row[f"{metric}_count"] = stats.get("count")
    return row


def _load_significance_summary() -> dict[tuple[str, str, str], dict]:
    path = results_root() / "reports" / result_stage() / "significance_summary.json"
    if not path.exists():
        from experiments.scripts.generate_significance import main as generate_significance

        generate_significance()
    if not path.exists():
        return {}
    payload = _load_json(path)
    index: dict[tuple[str, str, str], dict] = {}
    for row in payload:
        key = (row.get("run_id"), row.get("system_id"), row.get("planner_mode"))
        metrics = row.get("metrics", {})
        for stats in metrics.values():
            stats.setdefault("sampling_unit", row.get("sampling_unit"))
            stats.setdefault("test_method", row.get("test_method"))
        index[key] = metrics
    return index


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


def main():
    root = results_root() / "aggregated_metrics"
    summary_paths = sorted(root.rglob("metrics.summary.json"))
    rows = [_flatten_summary(path) for path in summary_paths]
    by_key = {(row["run_id"], row["system_id"], row["planner_mode"]): row for row in rows}
    b0 = by_key.get((configured_run_id("baseline"), "B0", "oracle"))
    if b0 and b0.get("CB_mean") and b0.get("TSR_mean"):
        for row in rows:
            cb = row.get("CB_mean")
            tsr = row.get("TSR_mean")
            if row.get("planner_mode") == "oracle" and isinstance(cb, (int, float)) and isinstance(tsr, (int, float)):
                row["CE_mean"] = ((b0["CB_mean"] - cb) / b0["CB_mean"]) * (tsr / b0["TSR_mean"])
    significance_by_key = _load_significance_summary()
    output_rows = []
    oracle_ours_key = (configured_run_id("oracle"), "Ours", "oracle")
    for row in rows:
        if row["system_id"] == "Ours":
            continue
        if row["run_id"] == configured_run_id("baseline"):
            ours_key = oracle_ours_key
        elif row["run_id"] == configured_run_id("ablation"):
            ours_key = oracle_ours_key
        else:
            continue
        ours = by_key.get(ours_key)
        if not ours:
            continue
        result = {
            "run_id": row["run_id"],
            "system_id": row["system_id"],
            "planner_mode": row["planner_mode"],
        }
        for key, value in row.items():
            if not key.endswith("_mean"):
                continue
            result[key] = value
            ours_value = ours.get(key)
            if isinstance(value, (int, float)) and isinstance(ours_value, (int, float)):
                result[f"{key}_delta_vs_ours"] = value - ours_value
        comparison = significance_by_key.get((row["run_id"], row["system_id"], row["planner_mode"]), {})
        for metric_name, stats in comparison.items():
            result[f"{metric_name}_paired_count"] = stats.get("paired_count")
            result[f"{metric_name}_cohen_d_vs_ours"] = stats.get("cohen_d")
            result[f"{metric_name}_p_value_vs_ours"] = stats.get("p_value")
            result[f"{metric_name}_holm_adjusted_p_vs_ours"] = stats.get("holm_adjusted_p")
            result[f"{metric_name}_sampling_unit"] = stats.get("sampling_unit")
            result[f"{metric_name}_test_method"] = stats.get("test_method")
        output_rows.append(result)

    report_root = results_root() / "reports" / result_stage()
    json_path = report_root / "statistics_summary.json"
    csv_path = report_root / "statistics_summary.csv"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(csv_path, output_rows)
    print(json_path)
    print(csv_path)


if __name__ == "__main__":
    main()
