from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _trace_usage(trace: dict) -> tuple[int, int, int, int]:
    calls = 0
    prompt = completion = total = 0
    for item in trace.get("agent_usage_metadata", []):
        calls += 1
        prompt += int(item.get("prompt_tokens", 0) or 0)
        completion += int(item.get("completion_tokens", 0) or 0)
        total += int(item.get("total_tokens", 0) or 0)
    return calls, prompt, completion, total


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate v4 pilot cost from project-local real-LLM traces.")
    parser.add_argument("--pilot-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--formal-matrix",
        type=Path,
        default=REPO_ROOT / "experiments" / "configs" / "protocol_v4_formal_agent_matrix.json",
        help="Optional frozen formal matrix used only for cost extrapolation.",
    )
    args = parser.parse_args()

    traces = []
    for path in sorted((args.pilot_root / "raw_traces").glob("**/*.json")):
        if path.name.endswith(".maintenance.json"):
            continue
        trace = _load(path)
        if trace.get("agent_backend") == "external_llm" and not trace.get("agent_failures"):
            traces.append((path, trace))

    rows = []
    for path, trace in traces:
        calls, prompt, completion, total = _trace_usage(trace)
        rows.append(
            {
                "trace": str(path.relative_to(args.pilot_root)),
                "scenario_id": trace.get("scenario_id"),
                "system_id": trace.get("system_id"),
                "seed": trace.get("seed"),
                "api_calls": calls,
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": total,
                "external_task_success": trace.get("external_task_success"),
            }
        )

    matrix = _load(REPO_ROOT / "experiments/configs/protocol_v4_pilot_matrix.json")
    longitudinal = _load(REPO_ROOT / "experiments/configs/protocol_v4_longitudinal_matrix.json")
    formal = _load(args.formal_matrix) if args.formal_matrix.exists() else {}
    observed = len(rows)
    mean = lambda key: sum(row[key] for row in rows) / observed if observed else 0.0
    report = {
        "protocol": "v4",
        "observed_valid_external_units": observed,
        "observed_rows": rows,
        "pilot_matrix_units": matrix.get("unit_count"),
        "longitudinal_held_out_units": longitudinal.get("unit_count"),
        "pilot_extrapolation": {
            "mean_api_calls_per_unit": mean("api_calls"),
            "mean_prompt_tokens_per_unit": mean("prompt_tokens"),
            "mean_completion_tokens_per_unit": mean("completion_tokens"),
            "mean_total_tokens_per_unit": mean("total_tokens"),
            "estimated_api_calls_for_pilot_matrix": math.ceil(mean("api_calls") * matrix.get("unit_count", 0)),
            "estimated_prompt_tokens_for_pilot_matrix": round(mean("prompt_tokens") * matrix.get("unit_count", 0)),
            "estimated_completion_tokens_for_pilot_matrix": round(mean("completion_tokens") * matrix.get("unit_count", 0)),
            "estimated_total_tokens_for_pilot_matrix": round(mean("total_tokens") * matrix.get("unit_count", 0)),
        },
        "longitudinal_extrapolation": {
            "estimated_total_tokens_if_same_mean": round(mean("total_tokens") * longitudinal.get("unit_count", 0)),
        },
        "formal_agent_extrapolation": {
            "matrix_path": str(args.formal_matrix),
            "formal_unit_count": formal.get("unit_count"),
            "estimated_api_calls_if_same_mean": math.ceil(mean("api_calls") * formal.get("unit_count", 0)),
            "estimated_prompt_tokens_if_same_mean": round(mean("prompt_tokens") * formal.get("unit_count", 0)),
            "estimated_completion_tokens_if_same_mean": round(mean("completion_tokens") * formal.get("unit_count", 0)),
            "estimated_total_tokens_if_same_mean": round(mean("total_tokens") * formal.get("unit_count", 0)),
        },
        "caveat": "This is a token/call estimate from 3 representative Ours units, not a performance result or a commitment to run the full matrix.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
