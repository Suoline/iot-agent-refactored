from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _expected_units(matrix: dict, run_id: str, group_id: str | None) -> list[dict]:
    units = matrix["units"]
    if group_id is not None:
        units = [unit for unit in units if unit["group_id"] == group_id]
    expected = []
    for unit in units:
        expected.append(
            {
                **unit,
                "key": f"{unit['group_id']}::{unit['system_id']}::{unit['planner_mode']}::{unit['scenario_id']}::{unit['seed']}",
                "manifest_glob": f"reports/{run_id}/{unit['system_id']}/{unit['planner_mode']}/{unit['scenario_id']}/{unit['seed']}.manifest.json",
            }
        )
    return expected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(REPO_ROOT / "experiments" / "configs" / "strict_experiment_matrix.json"))
    parser.add_argument("--results-root", default=str(REPO_ROOT / "experiments" / "results" / "strict_serial"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", default=None)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()

    matrix = _load_json(Path(args.matrix))
    results_root = Path(args.results_root)
    expected = _expected_units(matrix, args.run_id, args.group_id)
    observed = []
    missing_units = []
    missing_trace_files = []
    fallback_units = []
    failing_units = []
    revisions = Counter()
    total_calls = 0
    total_tokens = 0
    total_latency_ms = 0.0

    for unit in expected:
        manifest_path = results_root / unit["manifest_glob"]
        if not manifest_path.exists():
            missing_units.append(unit["key"])
            continue
        manifest = _load_json(manifest_path)
        observed.append(manifest)
        revisions[manifest.get("git_revision")] += 1
        total_calls += int(manifest.get("agent_api_call_count", 0) or 0)
        total_tokens += int((manifest.get("agent_usage_totals") or {}).get("total_tokens", 0) or 0)
        total_latency_ms += float(manifest.get("agent_latency_ms_sum", 0.0) or 0.0)
        trace_path = results_root / manifest["trace_file"]
        if not trace_path.exists():
            missing_trace_files.append(manifest["trace_file"])
        strict_checks = manifest.get("strict_checks", {})
        if (
            manifest.get("expected_agent_backend") == "external_llm"
            and manifest.get("agent_backend") == "heuristic_fallback"
        ):
            fallback_units.append(unit["key"])
        if not all(strict_checks.values()):
            failing_units.append(
                {
                    "key": unit["key"],
                    "strict_checks": strict_checks,
                    "manifest_file": str(manifest_path.relative_to(results_root)),
                }
            )

    incomplete_grid = bool(missing_units)
    mixed_revision = len([item for item in revisions if item not in {None, "unknown"}]) > 1
    failures = []
    if missing_trace_files:
        failures.append({"code": "missing_trace_files", "items": missing_trace_files})
    if fallback_units:
        failures.append({"code": "heuristic_fallback_detected", "items": fallback_units})
    if mixed_revision:
        failures.append({"code": "mixed_git_revisions", "items": dict(revisions)})
    if failing_units:
        failures.append({"code": "strict_checks_failed", "items": failing_units})
    if incomplete_grid and not args.allow_partial:
        failures.append({"code": "incomplete_grid", "items": missing_units})

    status = "pass"
    if failures:
        status = "fail"
    elif incomplete_grid:
        status = "partial"

    report = {
        "run_id": args.run_id,
        "group_id": args.group_id,
        "status": status,
        "allow_partial": args.allow_partial,
        "expected_unit_count": len(expected),
        "observed_unit_count": len(observed),
        "missing_unit_count": len(missing_units),
        "missing_units": missing_units[:200],
        "failure_count": len(failures),
        "failures": failures,
        "git_revisions": dict(revisions),
        "usage_summary": {
            "agent_api_call_count": total_calls,
            "agent_total_tokens": total_tokens,
            "agent_total_latency_ms": total_latency_ms,
        },
    }
    if args.group_id:
        output = results_root / "reports" / args.run_id / f"{args.group_id}.strict_audit.json"
    else:
        output = results_root / "reports" / args.run_id / "strict_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    if status == "fail":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
