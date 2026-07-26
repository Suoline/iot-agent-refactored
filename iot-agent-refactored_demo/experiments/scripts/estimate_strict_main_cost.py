from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _trace_usage_totals(trace: dict) -> dict[str, int]:
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }
    for batch in trace.get("agent_usage_metadata", []):
        prompt_tokens = batch.get("prompt_tokens", batch.get("input_tokens", 0))
        completion_tokens = batch.get("completion_tokens", batch.get("output_tokens", 0))
        total_tokens = batch.get("total_tokens", 0)
        if isinstance(prompt_tokens, (int, float)):
            totals["prompt_tokens"] += int(prompt_tokens)
            totals["input_tokens"] += int(prompt_tokens)
        if isinstance(completion_tokens, (int, float)):
            totals["completion_tokens"] += int(completion_tokens)
            totals["output_tokens"] += int(completion_tokens)
        if isinstance(total_tokens, (int, float)):
            totals["total_tokens"] += int(total_tokens)
    return totals


def _safe_mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(REPO_ROOT / "experiments" / "configs" / "strict_experiment_matrix.json"))
    parser.add_argument("--audit", required=True)
    parser.add_argument("--results-root", default=str(REPO_ROOT / "experiments" / "results" / "strict_serial"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", default="strict_main_agent")
    args = parser.parse_args()

    matrix = _load_json(Path(args.matrix))
    audit = _load_json(Path(args.audit))
    results_root = Path(args.results_root)
    main_group = next(group for group in matrix["groups"] if group["group_id"] == args.group_id)
    manifests = []
    for unit in matrix["units"]:
        if unit["group_id"] != args.group_id:
            continue
        manifest_path = (
            results_root
            / "reports"
            / args.run_id
            / unit["system_id"]
            / unit["planner_mode"]
            / unit["scenario_id"]
            / f"{unit['seed']}.manifest.json"
        )
        if manifest_path.exists():
            manifests.append(_load_json(manifest_path))

    call_counts = [float(item.get("agent_api_call_count", 0) or 0) for item in manifests]
    prompt_tokens = []
    completion_tokens = []
    input_tokens = []
    output_tokens = []
    total_tokens = []
    for item in manifests:
        trace_path = results_root / item["trace_file"]
        if trace_path.exists():
            totals = _trace_usage_totals(_load_json(trace_path))
        else:
            totals = item.get("agent_usage_totals") or {}
        prompt_tokens.append(float(totals.get("prompt_tokens", totals.get("input_tokens", 0)) or 0))
        completion_tokens.append(float(totals.get("completion_tokens", totals.get("output_tokens", 0)) or 0))
        input_tokens.append(float(totals.get("input_tokens", totals.get("prompt_tokens", 0)) or 0))
        output_tokens.append(float(totals.get("output_tokens", totals.get("completion_tokens", 0)) or 0))
        total_tokens.append(float(totals.get("total_tokens", 0) or 0))
    latencies = [float(item.get("agent_latency_ms_sum", 0.0) or 0.0) for item in manifests]

    total_units = main_group["unit_count"]
    mean_calls = _safe_mean(call_counts)
    mean_prompt = _safe_mean(prompt_tokens)
    mean_completion = _safe_mean(completion_tokens)
    mean_input = _safe_mean(input_tokens)
    mean_output = _safe_mean(output_tokens)
    mean_total = _safe_mean(total_tokens)
    mean_latency = _safe_mean(latencies)
    min_total = min(total_tokens) if total_tokens else 0.0
    max_total = max(total_tokens) if total_tokens else 0.0

    estimate = {
        "run_id": args.run_id,
        "group_id": args.group_id,
        "observed_unit_count": len(manifests),
        "expected_unit_count": total_units,
        "pilot_audit_status": audit.get("status"),
        "extrapolation": {
            "mean_agent_api_calls_per_unit": mean_calls,
            "mean_prompt_tokens_per_unit": mean_prompt,
            "mean_completion_tokens_per_unit": mean_completion,
            "mean_input_tokens_per_unit": mean_input,
            "mean_output_tokens_per_unit": mean_output,
            "mean_total_tokens_per_unit": mean_total,
            "mean_latency_ms_per_unit": mean_latency,
            "estimated_total_agent_api_calls": math.ceil(mean_calls * total_units),
            "estimated_total_input_tokens": int(round(mean_input * total_units)),
            "estimated_total_output_tokens": int(round(mean_output * total_units)),
            "estimated_total_tokens": int(round(mean_total * total_units)),
            "estimated_total_latency_ms_serial": mean_latency * total_units,
            "estimated_total_tokens_low": int(round(min_total * total_units)),
            "estimated_total_tokens_high": int(round(max_total * total_units)),
        },
        "pricing_formula": {
            "input_price_per_1m_tokens": os.environ.get("EXPERIMENT_AGENT_INPUT_PRICE_PER_1M"),
            "output_price_per_1m_tokens": os.environ.get("EXPERIMENT_AGENT_OUTPUT_PRICE_PER_1M"),
            "note": "若未提供单价，则本报告仅输出 token 与调用量估算。",
        },
    }
    input_price = estimate["pricing_formula"]["input_price_per_1m_tokens"]
    output_price = estimate["pricing_formula"]["output_price_per_1m_tokens"]
    if input_price is not None and output_price is not None:
        input_unit = float(input_price)
        output_unit = float(output_price)
        estimate["estimated_cost_range"] = {
            "mean_cost": (estimate["extrapolation"]["estimated_total_input_tokens"] / 1_000_000) * input_unit
            + (estimate["extrapolation"]["estimated_total_output_tokens"] / 1_000_000) * output_unit,
            "low_cost": (estimate["extrapolation"]["estimated_total_tokens_low"] / 1_000_000) * input_unit,
            "high_cost": (estimate["extrapolation"]["estimated_total_tokens_high"] / 1_000_000) * output_unit,
        }

    output = results_root / "reports" / args.run_id / f"{args.group_id}.cost_estimate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(estimate, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
