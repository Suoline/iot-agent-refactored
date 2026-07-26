from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.runner.scenario_loader import load_scenario
from experiments.runner.single_run import run_agent_scenario, run_oracle_scenario
from experiments.runner.system_registry import build_system_registry
from experiments.trace.writer import TraceWriter


def _git_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            cwd=REPO_ROOT,
        ).strip()
    except Exception:
        return "unknown"


def _load_matrix(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _usage_totals(trace: dict) -> dict[str, int]:
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


def _unit_paths(results_root: Path, run_id: str, system_id: str, planner_mode: str, scenario_id: str, seed: int) -> dict[str, Path]:
    base = results_root / "reports" / run_id / system_id / planner_mode / scenario_id
    raw = results_root / "raw_traces" / run_id / system_id / planner_mode / scenario_id / f"{seed}.json"
    maintenance = results_root / "raw_traces" / run_id / system_id / planner_mode / scenario_id / f"{seed}.maintenance.json"
    manifest = base / f"{seed}.manifest.json"
    return {"raw": raw, "maintenance": maintenance, "manifest": manifest}


def _has_transport_failure(trace: dict) -> bool:
    return any(
        str(item).startswith(("external_call_failed:", "external_init_failed:"))
        for item in trace.get("agent_failures", [])
    )


def _resume_validation(
    *,
    paths: dict[str, Path],
    raw_relative: str,
    maintenance_relative: str,
    manifest_relative: str,
    expected_backend: str | None,
    planner_mode: str,
    scenario_id: str,
    seed: int,
) -> dict:
    issues: list[str] = []
    if not all(path.exists() for path in paths.values()):
        missing = [name for name, path in paths.items() if not path.exists()]
        return {"complete": False, "issues": [f"missing:{name}" for name in missing]}

    try:
        trace = _load_json(paths["raw"])
    except Exception as exc:
        return {"complete": False, "issues": [f"invalid_raw:{type(exc).__name__}"]}
    try:
        maintenance = _load_json(paths["maintenance"])
    except Exception as exc:
        return {"complete": False, "issues": [f"invalid_maintenance:{type(exc).__name__}"]}
    try:
        manifest = _load_json(paths["manifest"])
    except Exception as exc:
        return {"complete": False, "issues": [f"invalid_manifest:{type(exc).__name__}"]}

    if trace.get("scenario_id") != scenario_id:
        issues.append("trace_scenario_mismatch")
    if trace.get("seed") != seed:
        issues.append("trace_seed_mismatch")
    if planner_mode == "agent" and not trace.get("agent_backend"):
        issues.append("trace_missing_agent_backend")
    if not isinstance(maintenance.get("maintenance_events"), list):
        issues.append("maintenance_events_invalid")
    if manifest.get("trace_file") != raw_relative:
        issues.append("manifest_trace_path_mismatch")
    if manifest.get("maintenance_file") != maintenance_relative:
        issues.append("manifest_maintenance_path_mismatch")
    if str(manifest.get("manifest_file", manifest_relative)) != manifest_relative:
        issues.append("manifest_manifest_path_mismatch")
    strict_checks = manifest.get("strict_checks")
    if not isinstance(strict_checks, dict) or not strict_checks:
        issues.append("manifest_strict_checks_missing")
    elif not all(bool(value) for value in strict_checks.values()):
        issues.append("manifest_strict_checks_failed")
    if expected_backend is not None and manifest.get("agent_backend") != expected_backend:
        issues.append("manifest_agent_backend_mismatch")
    if expected_backend is not None and trace.get("agent_backend") != expected_backend:
        issues.append("trace_agent_backend_mismatch")

    return {
        "complete": not issues,
        "issues": issues,
        "manifest": manifest,
        "trace": trace,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(REPO_ROOT / "experiments" / "configs" / "strict_experiment_matrix.json"))
    parser.add_argument("--results-root", default=str(REPO_ROOT / "experiments" / "results" / "strict_serial"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--system-id", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--planner-mode", required=True, choices=["oracle", "agent"])
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--agent-backend", choices=["external", "heuristic"], default=None)
    parser.add_argument("--require-agent-backend", default=None)
    args = parser.parse_args()

    matrix = _load_matrix(Path(args.matrix))
    matching_units = [
        unit
        for unit in matrix["units"]
        if unit["group_id"] == args.group_id
        and unit["system_id"] == args.system_id
        and unit["scenario_id"] == args.scenario_id
        and unit["seed"] == args.seed
        and unit["planner_mode"] == args.planner_mode
    ]
    if not matching_units:
        raise SystemExit("requested unit not found in strict experiment matrix")
    unit = matching_units[0]

    results_root = Path(args.results_root)
    paths = _unit_paths(results_root, args.run_id, args.system_id, args.planner_mode, args.scenario_id, args.seed)
    raw_relative = f"raw_traces/{args.run_id}/{args.system_id}/{args.planner_mode}/{args.scenario_id}/{args.seed}.json"
    maintenance_relative = f"raw_traces/{args.run_id}/{args.system_id}/{args.planner_mode}/{args.scenario_id}/{args.seed}.maintenance.json"
    manifest_relative = f"reports/{args.run_id}/{args.system_id}/{args.planner_mode}/{args.scenario_id}/{args.seed}.manifest.json"
    if any(path.exists() for path in paths.values()):
        if args.resume:
            resume_state = _resume_validation(
                paths=paths,
                raw_relative=raw_relative,
                maintenance_relative=maintenance_relative,
                manifest_relative=manifest_relative,
                expected_backend=args.require_agent_backend,
                planner_mode=args.planner_mode,
                scenario_id=args.scenario_id,
                seed=args.seed,
            )
            if resume_state["complete"]:
                print(
                    json.dumps(
                        {
                            "status": "skipped_existing",
                            "paths": {k: str(v) for k, v in paths.items()},
                            "resume_validation": "complete",
                        },
                        ensure_ascii=False,
                    )
                )
                return
            print(
                json.dumps(
                    {
                        "status": "resume_repair_required",
                        "paths": {k: str(v) for k, v in paths.items()},
                        "resume_issues": resume_state["issues"],
                    },
                    ensure_ascii=False,
                )
            )
        else:
            raise SystemExit("output already exists; rerun with --resume or choose a new run-id")

    previous_backend = os.environ.get("EXPERIMENT_AGENT_BACKEND")
    try:
        if args.agent_backend:
            os.environ["EXPERIMENT_AGENT_BACKEND"] = args.agent_backend
        scenario_path = REPO_ROOT / unit["scenario_path"]
        scenario = load_scenario(scenario_path)
        scenario["planner_mode"] = args.planner_mode
        registry = build_system_registry()
        system_config = registry[args.system_id]
        trace = (
            run_agent_scenario(scenario, seed=args.seed, system_config=system_config)
            if args.planner_mode == "agent"
            else run_oracle_scenario(scenario, seed=args.seed, system_config=system_config)
        )
    finally:
        if previous_backend is None:
            os.environ.pop("EXPERIMENT_AGENT_BACKEND", None)
        else:
            os.environ["EXPERIMENT_AGENT_BACKEND"] = previous_backend

    writer = TraceWriter(results_root)
    writer.write_json(raw_relative, trace)
    writer.write_json(maintenance_relative, {"maintenance_events": trace.get("maintenance_events", [])})

    expected_backend = args.require_agent_backend
    strict_checks = {
        "trace_exists": True,
        "maintenance_exists": True,
        "task_trace_complete": bool(trace.get("task_id") and trace.get("scenario_id") and trace.get("seed") is not None),
        "expected_agent_backend_ok": (
            expected_backend is None or trace.get("agent_backend") == expected_backend
        ),
        "no_heuristic_fallback": (
            expected_backend is None or trace.get("agent_backend") != "heuristic_fallback"
        ),
        "no_transport_failure": not _has_transport_failure(trace),
        "no_mixed_revision": True,
    }
    manifest = {
        "run_id": args.run_id,
        "group_id": args.group_id,
        "system_id": args.system_id,
        "planner_mode": args.planner_mode,
        "scenario_id": args.scenario_id,
        "seed": args.seed,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(),
        "world_version": trace.get("world_version"),
        "system_policy_version": trace.get("system_policy_version"),
        "source_scenario_path": unit["scenario_path"],
        "source_planner_mode": unit["source_planner_mode"],
        "task_id": trace.get("task_id"),
        "outcome": trace.get("outcome"),
        "task_success": trace.get("task_success"),
        "trace_file": raw_relative,
        "maintenance_file": maintenance_relative,
        "manifest_file": manifest_relative,
        "expected_agent_backend": expected_backend,
        "agent_backend": trace.get("agent_backend"),
        "agent_model": trace.get("agent_model"),
        "agent_provider": trace.get("agent_provider"),
        "agent_requested_seed": trace.get("agent_requested_seed"),
        "agent_request_seed_supported": trace.get("agent_request_seed_supported"),
        "agent_request_seed_applied": trace.get("agent_request_seed_applied"),
        "agent_seed_protocol": trace.get("agent_seed_protocol", "replicate_id"),
        "agent_api_call_count": len(trace.get("agent_usage_metadata", [])),
        "agent_usage_totals": _usage_totals(trace),
        "agent_latency_ms_sum": sum(trace.get("agent_latencies_ms", [])),
        "agent_failures": trace.get("agent_failures", []),
        "strict_checks": strict_checks,
    }
    writer.write_json(manifest_relative, manifest)
    failing_checks = [name for name, ok in strict_checks.items() if not ok]
    payload = {
        "status": "strict_check_failed" if failing_checks else "ok",
        "manifest_file": manifest_relative,
        "trace_file": raw_relative,
        "maintenance_file": maintenance_relative,
        "failing_checks": failing_checks,
        "agent_backend": trace.get("agent_backend"),
        "agent_failures": trace.get("agent_failures", []),
        "task_success": trace.get("task_success"),
        "outcome": trace.get("outcome"),
        "agent_requested_seed": trace.get("agent_requested_seed"),
        "agent_request_seed_supported": trace.get("agent_request_seed_supported"),
        "agent_request_seed_applied": trace.get("agent_request_seed_applied"),
        "agent_seed_protocol": trace.get("agent_seed_protocol", "replicate_id"),
        "agent_usage_totals": manifest["agent_usage_totals"],
        "agent_api_call_count": manifest["agent_api_call_count"],
        "agent_raw_output_excerpt": (trace.get("agent_raw_outputs") or [""])[-1][:200],
    }
    print(json.dumps(payload, ensure_ascii=False))
    if failing_checks:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
