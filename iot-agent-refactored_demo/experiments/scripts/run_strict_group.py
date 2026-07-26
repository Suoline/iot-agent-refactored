from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.trace.writer import TraceWriter


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _select_units(
    matrix: dict,
    *,
    group_id: str,
    system_ids: set[str] | None,
    scenario_ids: set[str] | None,
    seeds: set[int] | None,
    limit: int | None,
) -> list[dict]:
    units = [unit for unit in matrix["units"] if unit["group_id"] == group_id]
    if system_ids is not None:
        units = [unit for unit in units if unit["system_id"] in system_ids]
    if scenario_ids is not None:
        units = [unit for unit in units if unit["scenario_id"] in scenario_ids]
    if seeds is not None:
        units = [unit for unit in units if unit["seed"] in seeds]
    units.sort(key=lambda item: (item["seed"], item["system_id"], item["scenario_id"]))
    if limit is not None:
        units = units[:limit]
    return units


def _run_unit_once(
    *,
    python_executable: str,
    run_id: str,
    results_root: Path,
    unit: dict,
    agent_backend: str | None,
    require_agent_backend: str | None,
    resume: bool,
) -> tuple[int, dict]:
    cmd = [
        python_executable,
        "experiments/scripts/run_strict_serial_unit.py",
        "--run-id",
        run_id,
        "--group-id",
        unit["group_id"],
        f"--system-id={unit['system_id']}",
        "--scenario-id",
        unit["scenario_id"],
        "--seed",
        str(unit["seed"]),
        "--planner-mode",
        unit["planner_mode"],
        "--results-root",
        str(results_root),
    ]
    if agent_backend is not None and unit["planner_mode"] == "agent":
        cmd.extend(["--agent-backend", agent_backend])
    if require_agent_backend is not None and unit["planner_mode"] == "agent":
        cmd.extend(["--require-agent-backend", require_agent_backend])
    if resume:
        cmd.append("--resume")
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        env=dict(os.environ),
    )
    payload: dict = {
        "raw_stdout": result.stdout.strip(),
        "raw_stderr": result.stderr.strip(),
    }
    if result.stdout.strip():
        try:
            payload.update(json.loads(result.stdout.strip().splitlines()[-1]))
        except json.JSONDecodeError:
            pass
    return result.returncode, payload


def _load_manifest_usage(results_root: Path, payload: dict) -> dict[str, int]:
    manifest_rel = payload.get("manifest_file")
    if not manifest_rel:
        return {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    manifest_path = results_root / manifest_rel
    if not manifest_path.exists():
        return payload.get("agent_usage_totals") or {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
    manifest = _load_json(manifest_path)
    return manifest.get("agent_usage_totals") or {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def _is_retryable_transport_failure(payload: dict) -> bool:
    if payload.get("status") != "strict_check_failed":
        return False
    if not any(str(item).startswith("external_call_failed:") for item in payload.get("agent_failures", [])):
        return False
    raw = str(payload.get("agent_raw_output_excerpt", "")).lower()
    retryable_markers = [
        "http_error:429",
        "http_error:500",
        "http_error:502",
        "http_error:503",
        "http_error:504",
        "timed out",
        "timeout",
        "url_error",
        "connection reset",
        "temporarily unavailable",
    ]
    return any(marker in raw for marker in retryable_markers)


def _new_usage_totals() -> dict[str, int]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
    }


def _merge_usage(totals: dict[str, int], delta: dict[str, int]) -> None:
    for key in totals:
        totals[key] += int(delta.get(key, 0) or 0)


def _summary_relative_path(run_id: str, group_id: str) -> str:
    return f"reports/{run_id}/{group_id}.group_run_summary.json"


def _build_summary(
    *,
    run_id: str,
    group_id: str,
    units: list[dict],
    completed: list[dict],
    failures: list[dict],
    skipped: list[dict],
    attempts: list[dict],
    current_concurrency: int,
    peak_concurrency: int,
    retry_count: int,
    stopped_due_to_budget: bool,
    stop_reason: str | None,
    usage_totals: dict[str, int],
    api_call_count: int,
) -> dict:
    return {
        "run_id": run_id,
        "group_id": group_id,
        "selected_unit_count": len(units),
        "completed_count": len(completed),
        "failure_count": len(failures),
        "skipped_count": len(skipped),
        "attempt_count": len(attempts),
        "retry_count": retry_count,
        "current_concurrency": current_concurrency,
        "peak_concurrency": peak_concurrency,
        "stopped_due_to_budget": stopped_due_to_budget,
        "stop_reason": stop_reason,
        "usage_summary": {
            "agent_api_call_count": api_call_count,
            "agent_input_tokens": usage_totals["input_tokens"],
            "agent_output_tokens": usage_totals["output_tokens"],
            "agent_prompt_tokens": usage_totals["prompt_tokens"],
            "agent_completion_tokens": usage_totals["completion_tokens"],
            "agent_total_tokens": usage_totals["total_tokens"],
        },
        "completed": completed,
        "failures": failures,
        "skipped": skipped,
        "attempts": attempts,
    }


def _write_summary(results_root: Path, summary: dict) -> Path:
    writer = TraceWriter(results_root)
    relative = _summary_relative_path(summary["run_id"], summary["group_id"])
    return writer.write_json(relative, summary)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(REPO_ROOT / "experiments" / "configs" / "strict_experiment_matrix.json"))
    parser.add_argument("--results-root", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--group-id", required=True)
    parser.add_argument("--systems", default=None, help="Comma-separated system ids")
    parser.add_argument("--scenarios", default=None, help="Comma-separated scenario ids")
    parser.add_argument("--seeds", default=None, help="Comma-separated integer seeds")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--agent-backend", default=None, choices=["external", "heuristic"])
    parser.add_argument("--require-agent-backend", default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--continue-on-failure", action="store_true")
    parser.add_argument("--max-concurrency", type=int, default=1)
    parser.add_argument("--min-concurrency", type=int, default=1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--backoff-base-seconds", type=float, default=2.0)
    parser.add_argument("--budget-api-calls", type=int, default=0)
    parser.add_argument("--budget-total-tokens", type=int, default=0)
    parser.add_argument("--stability-window", type=int, default=8)
    args = parser.parse_args()

    matrix = _load_json(Path(args.matrix))
    results_root = Path(args.results_root)
    results_root.mkdir(parents=True, exist_ok=True)
    system_ids = set(args.systems.split(",")) if args.systems else None
    scenario_ids = set(args.scenarios.split(",")) if args.scenarios else None
    seeds = {int(item) for item in args.seeds.split(",")} if args.seeds else None
    units = _select_units(
        matrix,
        group_id=args.group_id,
        system_ids=system_ids,
        scenario_ids=scenario_ids,
        seeds=seeds,
        limit=args.limit,
    )

    requested_concurrency = max(1, args.max_concurrency)
    current_concurrency = max(args.min_concurrency, requested_concurrency)
    peak_concurrency = current_concurrency
    completed: list[dict] = []
    failures: list[dict] = []
    skipped: list[dict] = []
    attempts: list[dict] = []
    usage_totals = _new_usage_totals()
    api_call_count = 0
    retry_count = 0
    stable_successes = 0
    stopped_due_to_budget = False
    stop_reason: str | None = None

    pending = [
        {
            "unit": unit,
            "attempt": 1,
        }
        for unit in units
    ]
    in_flight: dict[Future, dict] = {}

    with ThreadPoolExecutor(max_workers=requested_concurrency) as executor:
        while pending or in_flight:
            while pending and len(in_flight) < current_concurrency and not stopped_due_to_budget:
                if args.budget_api_calls and api_call_count >= args.budget_api_calls:
                    stopped_due_to_budget = True
                    stop_reason = "api_call_budget_exceeded"
                    break
                if args.budget_total_tokens and usage_totals["total_tokens"] >= args.budget_total_tokens:
                    stopped_due_to_budget = True
                    stop_reason = "total_token_budget_exceeded"
                    break
                next_item = pending.pop(0)
                future = executor.submit(
                    _run_unit_once,
                    python_executable=sys.executable,
                    run_id=args.run_id,
                    results_root=results_root,
                    unit=next_item["unit"],
                    agent_backend=args.agent_backend,
                    require_agent_backend=args.require_agent_backend,
                    resume=args.resume,
                )
                in_flight[future] = next_item

            if not in_flight:
                break

            done, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                attempt_meta = in_flight.pop(future)
                unit = attempt_meta["unit"]
                returncode, payload = future.result()
                usage = _load_manifest_usage(results_root, payload)
                api_call_count += int(payload.get("agent_api_call_count", 0) or 0)
                _merge_usage(usage_totals, usage)
                attempt_event = {
                    "attempt": attempt_meta["attempt"],
                    "unit": unit,
                    "returncode": returncode,
                    "payload": payload,
                    "retryable_transport_failure": _is_retryable_transport_failure(payload),
                }
                attempts.append(attempt_event)

                is_retryable = attempt_event["retryable_transport_failure"]
                if returncode == 0 and payload.get("status") == "skipped_existing":
                    skipped.append(attempt_event)
                    stable_successes += 1
                elif returncode == 0:
                    completed.append(attempt_event)
                    stable_successes += 1
                elif is_retryable and attempt_meta["attempt"] <= args.max_retries:
                    retry_count += 1
                    stable_successes = 0
                    current_concurrency = max(args.min_concurrency, current_concurrency // 2 or 1)
                    time.sleep(args.backoff_base_seconds * (2 ** (attempt_meta["attempt"] - 1)))
                    pending.insert(
                        0,
                        {
                            "unit": unit,
                            "attempt": attempt_meta["attempt"] + 1,
                        },
                    )
                else:
                    failures.append(attempt_event)
                    stable_successes = 0
                    if not args.continue_on_failure:
                        pending.clear()

                if (
                    stable_successes >= args.stability_window
                    and current_concurrency < requested_concurrency
                ):
                    current_concurrency += 1
                    peak_concurrency = max(peak_concurrency, current_concurrency)
                    stable_successes = 0

                peak_concurrency = max(peak_concurrency, current_concurrency)
                summary = _build_summary(
                    run_id=args.run_id,
                    group_id=args.group_id,
                    units=units,
                    completed=completed,
                    failures=failures,
                    skipped=skipped,
                    attempts=attempts,
                    current_concurrency=current_concurrency,
                    peak_concurrency=peak_concurrency,
                    retry_count=retry_count,
                    stopped_due_to_budget=stopped_due_to_budget,
                    stop_reason=stop_reason,
                    usage_totals=usage_totals,
                    api_call_count=api_call_count,
                )
                _write_summary(results_root, summary)

                if stopped_due_to_budget:
                    pending.clear()
                    break

    summary = _build_summary(
        run_id=args.run_id,
        group_id=args.group_id,
        units=units,
        completed=completed,
        failures=failures,
        skipped=skipped,
        attempts=attempts,
        current_concurrency=current_concurrency,
        peak_concurrency=peak_concurrency,
        retry_count=retry_count,
        stopped_due_to_budget=stopped_due_to_budget,
        stop_reason=stop_reason,
        usage_totals=usage_totals,
        api_call_count=api_call_count,
    )
    summary_path = _write_summary(results_root, summary)
    print(summary_path)
    if failures or stopped_due_to_budget:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
