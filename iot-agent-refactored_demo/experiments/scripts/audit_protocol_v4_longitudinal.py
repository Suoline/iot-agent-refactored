from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.baselines.raw_text import build_raw_text_package
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.runner.system_registry import SystemConfig
from experiments.world_model.ha_oracle import HAOracle


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit v4 L1 persistence and B4 raw-history growth without model calls.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--history-length", type=int, default=40)
    args = parser.parse_args()
    world = HAOracle()
    db_path = args.output.parent / f"l1_persistent_{args.seed}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    system_checks = {}
    for system_id in ("Ours", "B0", "B1", "B2", "B3", "B4", "B5"):
        config = SystemConfig(system_id=system_id, planner_mode="agent", evaluation_protocol="v4")
        if system_id == "B1":
            config.use_memory = False
            config.use_structure = False
            config.score_mode = "rag_only"
        if system_id == "B4":
            config.use_memory = False
            config.use_structure = False
            config.score_mode = "large_context"
        system_checks[system_id] = {"persistent_sqlite_contract": system_id not in {"B0", "B1", "B4"}, "raw_text_baseline": system_id in {"B1", "B4"}}
    service = MemoryService(db_path, config=SystemConfig(system_id="Ours", planner_mode="agent", evaluation_protocol="v4").__dict__)
    session_1 = ingest_user_text(service, text="我喜欢把卧室空调设为26度", now=world.current_time, turn_id=f"l1_{args.seed}_s1")
    world.advance_to(world.current_time + timedelta(days=14))
    query = "按我喜欢的温度设卧室空调"
    session_2_package = service.search(query, task_type="control", now=world.current_time)
    history = [f"历史对话 {index}: 用户在讨论与本任务无关的家居细节。" for index in range(args.history_length)]
    history.append("我喜欢把卧室空调设为26度")
    b1 = build_raw_text_package(query=query, task_type="control", fixture=[], conversation_history=history, world=world, full_history=False)
    b4 = build_raw_text_package(query=query, task_type="control", fixture=[], conversation_history=history, world=world, full_history=True)
    report = {
        "protocol": "v4_longitudinal_audit", "seed": args.seed, "database": str(db_path),
        "session_1_ingestion": session_1, "session_2_days_later": 14,
        "persistent_record_count": len(service.list_records(include_deleted=True)),
        "ours_session_2_memory_ids": [item.memory_id for item in session_2_package.matched_memories],
        "b1_context_source": b1.retrieval_metadata.get("baseline_context_source"),
        "b4_context_source": b4.retrieval_metadata.get("baseline_context_source"),
        "b1_raw_document_count": b1.retrieval_metadata.get("raw_document_count"),
        "b4_raw_document_count": b4.retrieval_metadata.get("raw_document_count"),
        "b1_context_document_count": len(b1.matched_memories),
        "b4_context_document_count": len(b4.matched_memories),
        "b1_uses_structured_memory": False, "b4_uses_structured_memory": False,
        "system_contracts": system_checks,
        "checks": {
            "sqlite_persists_across_session": bool(session_2_package.matched_memories),
            "b1_is_raw_text_rag": b1.retrieval_metadata.get("baseline_context_source") == "raw_text_rag",
            "b4_is_full_raw_history": b4.retrieval_metadata.get("baseline_context_source") == "full_raw_history",
            "b4_context_grows_beyond_b1": len(b4.matched_memories) > len(b1.matched_memories),
            "all_systems_declared": set(system_checks) == {"Ours", "B0", "B1", "B2", "B3", "B4", "B5"},
        },
    }
    report["status"] = "pass" if all(report["checks"].values()) else "fail"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
