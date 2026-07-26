from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


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
    units.sort(key=lambda item: (item["system_id"], item["scenario_id"], item["seed"]))
    if limit is not None:
        units = units[:limit]
    return units


def _run_unit(
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
    payload: dict = {"raw_stdout": result.stdout.strip(), "raw_stderr": result.stderr.strip()}
    if result.stdout.strip():
        try:
            payload.update(json.loads(result.stdout.strip().splitlines()[-1]))
        except json.JSONDecodeError:
            pass
    return result.returncode, payload


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
    completed = []
    failures = []
    for index, unit in enumerate(units, start=1):
        returncode, payload = _run_unit(
            python_executable=sys.executable,
            run_id=args.run_id,
            results_root=results_root,
            unit=unit,
            agent_backend=args.agent_backend,
            require_agent_backend=args.require_agent_backend,
            resume=args.resume,
        )
        event = {
            "index": index,
            "unit": unit,
            "returncode": returncode,
            "payload": payload,
        }
        if returncode == 0:
            completed.append(event)
        else:
            failures.append(event)
            if not args.continue_on_failure:
                break
    summary = {
        "run_id": args.run_id,
        "group_id": args.group_id,
        "selected_unit_count": len(units),
        "completed_count": len(completed),
        "failure_count": len(failures),
        "completed": completed,
        "failures": failures,
    }
    summary_path = results_root / "reports" / args.run_id / f"{args.group_id}.group_run_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(summary_path)
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
