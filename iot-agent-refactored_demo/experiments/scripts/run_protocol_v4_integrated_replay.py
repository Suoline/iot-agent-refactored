from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.adapters.agent_adapter import AgentAdapter
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.runner.single_run import _execute_actions, _infer_control_action, _inject_registry_candidates
from experiments.runner.system_registry import SystemConfig
from experiments.world_model.ha_oracle import HAOracle


def main() -> None:
    parser = argparse.ArgumentParser(description="Run raw-text v4 ingestion/update/replay without gold memory operations.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=1001)
    parser.add_argument("--backend", choices=["external", "heuristic"], default="heuristic")
    args = parser.parse_args()
    if args.backend == "external":
        import os
        os.environ["EXPERIMENT_AGENT_BACKEND"] = "external"
    world = HAOracle()
    db_path = args.output.parent / f"integrated_replay_{args.seed}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    service = MemoryService(db_path, config=SystemConfig(system_id="Ours", planner_mode="agent", evaluation_protocol="v4").__dict__)
    first = ingest_user_text(service, text="我喜欢把卧室空调设为24度", now=world.current_time, turn_id="u1")
    world.advance_to(world.current_time + timedelta(days=1))
    correction = ingest_user_text(service, text="不对，我喜欢把卧室空调改成26度", now=world.current_time, turn_id="u2")
    query = "按我喜欢的温度设卧室空调"
    package = _inject_registry_candidates(world, service.search(query, task_type="control", now=world.current_time), query)
    decision = AgentAdapter().plan(package, query, requested_seed=args.seed)
    actions = list(decision.actions or ([decision.action] if decision.action else []))
    if actions and actions[0].get("service") == "planner.select":
        inferred = _infer_control_action(query, package, world)
        actions = [inferred] if inferred else []
    success, state, execution = _execute_actions(world, actions) if actions else (False, {}, [])
    report = {
        "protocol": "v4_integrated_replay", "input_mode": "raw_user_text", "forbidden_inputs_absent": True,
        "db_path": str(db_path), "ingestion": [first, correction], "query": query,
        "agent_backend": decision.backend, "raw_output": decision.raw_output,
        "guarded_plan": (decision.structured_output or {}).get("guarded_plan"),
        "usage": decision.usage, "seed_protocol": decision.seed_protocol,
        "actions": actions, "execution": execution, "execution_success": success, "final_state": state,
        "records": [record.model_dump(mode="json") for record in service.list_records(include_deleted=True)],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
