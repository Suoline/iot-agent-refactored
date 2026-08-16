from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.metrics.core import task_metrics


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate valid v4 external-LLM traces by system/scenario/seed.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    rows = []
    for path in sorted(args.root.glob("raw_traces/**/*.json")):
        if path.name.endswith(".maintenance.json"):
            continue
        trace = json.loads(path.read_text(encoding="utf-8"))
        if trace.get("evaluation_protocol") != "v4" or trace.get("agent_backend") != "external_llm" or trace.get("agent_failures"):
            continue
        metric = task_metrics(trace)
        rows.append({
            "system_id": trace.get("system_id"), "scenario_id": trace.get("scenario_id"), "seed": trace.get("seed"),
            "TSR": metric["TSR"], "Contract Conformance Score": metric["Contract Conformance Score"],
            "WDR": metric["WDR"], "CB": metric["CB"], "PM": metric["PM"], "UAA": metric["UAA"],
            "Control Final-State TSR": metric["Control Final-State TSR"],
            "Query Answer Accuracy": metric["Query Answer Accuracy"],
            "Automation Decision Accuracy": metric["Automation Decision Accuracy"],
            "Unsafe Action Rate": metric["Unsafe Action Rate"],
            "Necessary Clarification Rate": metric["Necessary Clarification Rate"],
            "Unnecessary Clarification Rate": metric["Unnecessary Clarification Rate"],
            "Prompt Tokens": metric["Prompt Tokens"], "Completion Tokens": metric["Completion Tokens"],
            "Latency ms": metric["end_to_end_latency_ms"], "trace": str(path.relative_to(args.root)),
        })
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["system_id"]].append(row)
    summary = []
    for system_id, group in sorted(grouped.items()):
        result = {"system_id": system_id, "valid_unit_count": len(group)}
        for key in (
            "TSR", "Contract Conformance Score", "WDR", "CB", "PM", "UAA",
            "Control Final-State TSR", "Query Answer Accuracy", "Automation Decision Accuracy",
            "Unsafe Action Rate", "Necessary Clarification Rate", "Unnecessary Clarification Rate",
            "Prompt Tokens", "Completion Tokens", "Latency ms",
        ):
            result[key] = _mean([float(row[key]) for row in group if row[key] is not None])
        prompt = result["Prompt Tokens"]
        result["Context Efficiency"] = (result["TSR"] / prompt * 1000) if result["TSR"] is not None and prompt else None
        summary.append(result)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "per_unit.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = list(rows[0]) if rows else ["system_id", "scenario_id", "seed"]
    with (args.output_dir / "per_unit.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(args.output_dir)


if __name__ == "__main__":
    main()
