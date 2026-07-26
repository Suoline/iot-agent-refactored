from __future__ import annotations

import tempfile
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from experiments.memory.schemas import UsageEvent
from experiments.memory.schemas import CandidateDevice
from experiments.memory.vector_index import _tokens
from experiments.memory.service import MemoryService
from experiments.adapters.agent_adapter import AgentAdapter
from experiments.planners.oracle_planner import OraclePlanner
from experiments.runner.system_registry import SystemConfig
from experiments.trace.schemas import RetrievalStepTrace, RetrievedMemoryTrace, TaskTrace
from experiments.trace.schemas import MaintenanceTrace
from experiments.world_model.ha_oracle import HAOracle


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 4)


def _parse_sim_time(t0: datetime, raw: str | None) -> datetime:
    if not raw:
        return t0
    raw = raw.strip()
    if raw.startswith("t0+"):
        offset, clock = raw[3:].split(" ", 1)
        days = int(offset[:-1])
        hour, minute = map(int, clock.split(":"))
        return (t0 + timedelta(days=days)).replace(hour=hour, minute=minute)
    if raw.startswith("t0 "):
        hour, minute = map(int, raw[3:].split(":"))
        return t0.replace(hour=hour, minute=minute)
    return datetime.fromisoformat(raw)


def _apply_custom_event(world: HAOracle, service: MemoryService, step: dict, now: datetime):
    kind = step.get("kind")
    payload = step.get("payload", {})
    if kind == "maintenance":
        for op in payload.get("memory_ops", []):
            service.apply_memory_op(op, now)
        started = time.perf_counter()
        result = service.maintenance(now)
        result["maintenance_latency_ms"] = (time.perf_counter() - started) * 1000
        changed = len(result.get("changed_memory_ids", []))
        result["estimated_maintenance_tokens"] = max(1, changed * 4) if changed else 1
        return result
    if kind == "behavior_observation":
        for op in payload.get("memory_ops", []):
            service.apply_memory_op(op, now)
        return
    if kind == "state_override":
        for entity_id, state in payload.get("states", {}).items():
            world.states[entity_id] = state
        return None
    if kind == "world_mutation":
        event_id = payload.get("event_id")
        if event_id:
            event = next(
                item for item in world.definition["events"] if item["event_id"] == event_id
            )
            world.apply_event({**event, "sim_time": now.isoformat()})
        return None
    return None


def _inject_registry_candidates(world: HAOracle, package, query: str):
    if package.candidate_devices:
        return _enrich_candidate_devices(world, package)
    memory_candidates = _memory_grounded_candidates(package)
    if memory_candidates:
        package.candidate_devices = memory_candidates
        return _enrich_candidate_devices(world, package)
    query_tokens = _tokens(query)
    candidates = []
    for entity_id, entity in world.entities.items():
        name = entity.get("display_name", entity_id)
        score_tokens = _tokens(f"{name} {entity_id}")
        overlap = len(query_tokens & score_tokens) / len(query_tokens | score_tokens or {"_"})
        if overlap > 0:
            candidates.append(
                {
                    "entity_id": entity_id,
                    "name": name,
                    "score": overlap,
                    "confidence": 0.95,
                    "entity_type": entity.get("type"),
                    "capabilities": entity.get("capabilities", []),
                    "available_services": _service_options_for_entity(entity_id, entity),
                    "current_state": world.get_state(entity_id) if entity_id in world.states else {},
                    "matched_memories": [],
                    "missing_info": [],
                }
            )
    candidates.sort(key=lambda item: (-item["score"], item["entity_id"]))
    package.candidate_devices = [
        CandidateDevice.model_validate(item)
        for item in candidates[:5]
    ]
    if candidates and package.should_ask_user:
        package.should_ask_user = False
        package.ask_reason = None
    return _enrich_candidate_devices(world, package)


def _service_options_for_entity(entity_id: str, entity: dict) -> list[str]:
    if entity_id.startswith("routine."):
        return ["routine.run"]
    entity_type = entity.get("type")
    if entity_type in {"light", "switch"}:
        return [f"{entity_type}.turn_on", f"{entity_type}.turn_off"]
    if entity_type == "cover":
        return ["cover.set_position"]
    if entity_type == "lock":
        return ["lock.lock", "lock.unlock"]
    if entity_type == "climate":
        return ["climate.set_temperature"]
    return []


def _memory_grounded_candidates(package) -> list[CandidateDevice]:
    grouped: dict[str, list] = {}
    display_names: dict[str, str] = {}
    for item in package.matched_memories:
        if not item.in_usable_set:
            continue
        entity_id = None
        if item.memory_type == "routine":
            if "routine." in item.text:
                for token in item.text.split():
                    if token.startswith("routine."):
                        entity_id = token
                        break
        if entity_id is None:
            entity_id = package.retrieval_metadata.get("memory_entity_map", {}).get(item.memory_id)
        if not entity_id:
            continue
        grouped.setdefault(entity_id, []).append(item)
        display_names.setdefault(entity_id, entity_id)
    candidates: list[CandidateDevice] = []
    for entity_id, related in grouped.items():
        best = max(related, key=lambda match: match.score)
        candidates.append(
            CandidateDevice(
                entity_id=entity_id,
                name=display_names.get(entity_id, entity_id),
                score=best.score,
                confidence=best.effective_confidence,
                matched_memories=sorted(related, key=lambda match: (-match.score, match.memory_id)),
            )
        )
    candidates.sort(key=lambda item: (-item.score, item.entity_id))
    return candidates[:5]


def _enrich_candidate_devices(world: HAOracle, package):
    enriched: list[CandidateDevice] = []
    for candidate in package.candidate_devices:
        entity = world.entities.get(candidate.entity_id, {})
        payload = candidate.model_dump(mode="json")
        payload["entity_type"] = entity.get("type", payload.get("entity_type") or ("routine" if candidate.entity_id.startswith("routine.") else None))
        payload["capabilities"] = entity.get("capabilities", payload.get("capabilities", []))
        payload["available_services"] = _service_options_for_entity(candidate.entity_id, entity)
        payload["current_state"] = world.get_state(candidate.entity_id) if candidate.entity_id in world.states else {}
        enriched.append(CandidateDevice.model_validate(payload))
    package.candidate_devices = enriched
    return package


def _execute_action(world: HAOracle, action: dict) -> tuple[bool, dict]:
    service = action["service"]
    entity_id = action["entity_id"]
    args = action.get("args", {})
    if service == "memory.answer":
        return True, {}
    if service == "routine.run" and entity_id == "routine.movie_mode":
        results = [
            world.apply("light.turn_off", {"entity": "light.living_ceiling"}, world.current_time),
            world.apply("light.turn_on", {"entity": "light.living_ambient"}, world.current_time),
            world.apply("cover.set_position", {"entity": "cover.living_curtain", "position": 0}, world.current_time),
        ]
        success = all(item.get("success") for item in results)
        state = {
            "light.living_ceiling": world.get_state("light.living_ceiling"),
            "light.living_ambient": world.get_state("light.living_ambient"),
            "cover.living_curtain": world.get_state("cover.living_curtain"),
        }
        return success, state
    result = world.apply(service, {"entity": entity_id, **args}, world.current_time)
    state = {entity_id: world.get_state(entity_id)} if result.get("success") and entity_id in world.states else {}
    return bool(result.get("success")), state


def _execute_actions(world: HAOracle, actions: list[dict]) -> tuple[bool, dict, list[dict]]:
    overall_success = True
    merged_state: dict = {}
    execution_results: list[dict] = []
    for action in actions:
        success, state = _execute_action(world, action)
        overall_success = overall_success and success
        merged_state.update(state)
        execution_results.append(
            {
                "action": action,
                "success": success,
                "result_state": state,
            }
        )
    return overall_success, merged_state, execution_results


def _infer_control_action(query: str, package, world: HAOracle) -> dict | None:
    if not package.candidate_devices:
        return None
    if len(package.candidate_devices) > 1 and (
        package.candidate_devices[0].score - package.candidate_devices[1].score < 0.10
    ):
        return None
    candidate = package.candidate_devices[0]
    if candidate.score < 0.4:
        return None
    entity = world.entities.get(candidate.entity_id)
    if not entity:
        return None
    capabilities = set(entity.get("capabilities", []))
    if any(keyword in query for keyword in ["暖色", "冷色", "色温", "调色温"]):
        if "color_temp" not in capabilities:
            return None
        return {
            "service": f"{entity['type']}.turn_on",
            "entity_id": candidate.entity_id,
            "args": {"color_temp": 2700},
        }
    if any(keyword in query for keyword in ["开", "打开", "只开"]):
        if entity["type"] in {"light", "switch"}:
            return {"service": f"{entity['type']}.turn_on", "entity_id": candidate.entity_id, "args": {}}
        if entity["type"] == "lock":
            return {"service": "lock.unlock", "entity_id": candidate.entity_id, "args": {}}
        if entity["type"] == "cover":
            return {"service": "cover.set_position", "entity_id": candidate.entity_id, "args": {"position": 100}}
    if any(keyword in query for keyword in ["关", "关闭"]):
        if entity["type"] in {"light", "switch"}:
            return {"service": f"{entity['type']}.turn_off", "entity_id": candidate.entity_id, "args": {}}
        if entity["type"] == "lock":
            return {"service": "lock.lock", "entity_id": candidate.entity_id, "args": {}}
        if entity["type"] == "cover":
            return {"service": "cover.set_position", "entity_id": candidate.entity_id, "args": {"position": 0}}
    if entity["type"] == "climate" and any(keyword in query for keyword in ["温度", "空调"]):
        import re

        match = re.search(r"(\d{2})", query)
        target = int(match.group(1)) if match else int(entity.get("attributes", {}).get("target_temp", 24))
        return {"service": "climate.set_temperature", "entity_id": candidate.entity_id, "args": {"temperature": target}}
    return None


def _load_fixture_records(service: MemoryService, fixtures: list[dict], now: datetime):
    for fixture in fixtures:
        status = fixture.get("status", "active")
        op = "add_active" if status == "active" else "add_candidate"
        service.apply_memory_op({**fixture, "op": op}, now)


def _write_failure_reflection(
    service: MemoryService,
    *,
    task_id: str,
    query: str,
    entity_id: str | None,
    now: datetime,
    reason: str,
):
    if not entity_id:
        return
    service.apply_memory_op(
        {
            "op": "add_active",
            "memory_id": f"{task_id}_reflection_{entity_id.replace('.', '_')}",
            "memory_type": "reflection",
            "scope": "entity",
            "subject": f"{query} 失败反思",
            "predicate": "future_rule",
            "object": "下次先确认或提示失败原因",
            "entity_id": entity_id,
            "source": "execution_verification",
            "half_life_days": 90,
            "natural_text": f"{query} 在 {entity_id} 上失败，原因：{reason}",
        },
        now,
    )


def _append_assertion_result(
    trace: TaskTrace,
    *,
    kind: str,
    step_id: str,
    success: bool,
    expected=None,
    observed=None,
    details=None,
):
    payload = {
        "kind": kind,
        "step_id": step_id,
        "success": success,
    }
    if expected is not None:
        payload["expected"] = expected
    if observed is not None:
        payload["observed"] = observed
    if details is not None:
        payload["details"] = details
    trace.assertion_results.append(payload)


def run_oracle_scenario(
    scenario: dict,
    *,
    seed: int = 1001,
    results_root: str | Path | None = None,
    system_config: SystemConfig | None = None,
) -> dict:
    started = time.perf_counter()
    world = HAOracle()
    world.reset()
    if scenario.get("initial_state_overrides"):
        for entity_id, override in scenario["initial_state_overrides"].items():
            world.states[entity_id] = override

    temp_db = Path(tempfile.gettempdir()) / f"{scenario['scenario_id']}_{seed}_{uuid.uuid4().hex}.sqlite3"
    system_config = system_config or SystemConfig(system_id="Ours")
    service = MemoryService(temp_db, config=system_config.__dict__)
    planner = OraclePlanner()

    trace = TaskTrace(
        task_id=f"{scenario['scenario_id']}_{seed}",
        scenario_id=scenario["scenario_id"],
        seed=seed,
        world_version=scenario["world_version"],
        system_policy_version=scenario.get("system_policy_version", "sp-v1"),
        planner_mode=scenario["planner_mode"],
        system_id=system_config.system_id,
        task_type=scenario.get("task_type", "control"),
        sim_time=world.current_time,
        safety_relevant=bool(scenario.get("safety_relevant", False)),
    )
    assertion_failures: list[str] = []
    last_decision = None
    last_query = ""
    last_task_type = scenario.get("task_type", "control")
    current_grounding_ids: list[str] = []
    assertion_status = {
        "action": {"seen": False, "success": True},
        "clarification": {"seen": False, "success": True},
        "memory": {"seen": False, "success": True},
        "final_state": {"seen": False, "success": True},
    }
    raw_history: list[str] = []

    _load_fixture_records(service, scenario.get("initial_memory_fixture", []), world.current_time)

    for index, step in enumerate(scenario.get("steps", []), start=1):
        step_type = step["type"]
        sim_time = _parse_sim_time(world.t0, step.get("sim_time"))
        if sim_time > world.current_time:
            world.advance_to(sim_time)
        trace.sim_time = world.current_time

        if step_type == "event":
            event_id = step.get("event_id")
            if event_id:
                event = next(
                    item for item in world.definition["events"] if item["event_id"] == event_id
                )
                event_time = _parse_sim_time(world.t0, step["sim_time"])
                world.apply_event({**event, "sim_time": event_time.isoformat()})
            else:
                before = {item.memory_id: item.status for item in service.list_records(include_deleted=True)}
                maintenance_result = _apply_custom_event(world, service, step, world.current_time)
                after = {item.memory_id: item.status for item in service.list_records(include_deleted=True)}
                changed = [memory_id for memory_id, status in after.items() if before.get(memory_id) != status]
                if changed or maintenance_result:
                    trace.usage_events.append({"kind": "maintenance", "changed_memory_ids": changed})
                if maintenance_result:
                    for memory_id in maintenance_result["resampled_memory_ids"]:
                        trace.usage_events.append({"kind": "resampled", "memory_id": memory_id})
                    trace.maintenance_events.append(
                        MaintenanceTrace(
                            maintenance_id=step.get("step_id", f"mnt_{index}"),
                            sim_time=world.current_time,
                            expired_memory_ids=maintenance_result["expired_memory_ids"],
                            stale_memory_ids=maintenance_result["stale_memory_ids"],
                            archived_memory_ids=maintenance_result["archived_memory_ids"],
                            resampled_memory_ids=maintenance_result["resampled_memory_ids"],
                            rollback_merge_ids=maintenance_result["rollback_merge_ids"],
                            needs_review_ids=maintenance_result.get("needs_review_ids", []),
                            deleted_by_capacity_ids=maintenance_result.get("deleted_by_capacity_ids", []),
                            maintenance_latency_ms=maintenance_result.get("maintenance_latency_ms", 0.0),
                            estimated_maintenance_tokens=maintenance_result.get("estimated_maintenance_tokens", 0),
                        )
                    )
            continue

        if step_type == "say":
            oracle_input = step.get("oracle_input", {})
            last_task_type = oracle_input.get("task_type", scenario.get("task_type", "control"))
            for op in oracle_input.get("memory_ops", []):
                service.apply_memory_op(op, world.current_time)
            query = step.get("text", "")
            last_query = query
            package = service.search(
                query,
                task_type=last_task_type,
                now=world.current_time,
            )
            package = _inject_registry_candidates(world, package, query)
            if system_config.score_mode == "large_context":
                trace.estimated_prompt_tokens += sum(_approx_tokens(text) for text in [*raw_history, query])
            else:
                trace.estimated_prompt_tokens += _approx_tokens(query) + sum(
                    _approx_tokens(item.text) for item in package.matched_memories
                )
            raw_history.append(query)
            retrieval_trace = RetrievalStepTrace(
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                stage="planning",
                query=query,
                retrieved_memories=[
                    RetrievedMemoryTrace(
                        memory_id=item.memory_id,
                        memory_type=item.memory_type,
                        rank=rank,
                        retrieval_score=item.score,
                        effective_confidence=item.effective_confidence,
                        memory_worth=item.memory_worth,
                        raw_confidence=item.raw_confidence,
                        system_status=item.system_status,
                        true_status=item.true_status,
                        runtime_status=item.runtime_status,
                        in_usable_set=item.in_usable_set,
                        in_grounding_set=False,
                    )
                    for rank, item in enumerate(package.matched_memories, start=1)
                ],
            )
            trace.steps.append(retrieval_trace)
            decision = planner.decide(package)
            last_decision = decision
            trace.should_ask_user = decision.should_ask_user
            action_template = oracle_input.get("action_template")
            memory_ops = oracle_input.get("memory_ops", [])
            inferred_action = None
            if not action_template and not memory_ops:
                inferred_action = _infer_control_action(query, package, world)
            if decision.should_ask_user:
                if inferred_action:
                    success, state = _execute_action(world, inferred_action)
                    if success:
                        trace.final_device_state.update(state)
                        trace.chosen_action = inferred_action
                        trace.should_ask_user = False
                    else:
                        assertion_failures.append(f"world.apply failed: {inferred_action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=inferred_action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                        trace.clarification_turns += 1
                        if trace.safety_relevant:
                            trace.safety_gated = True
                        trace.chosen_action = None
                else:
                    trace.clarification_turns += 1
                    if trace.safety_relevant:
                        trace.safety_gated = True
                    trace.chosen_action = None
            else:
                if decision.action and decision.action.get("service") == "planner.select" and action_template:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:1]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    actual_action = {
                        "service": action_template["service"],
                        "entity_id": decision.action["entity_id"],
                        "args": action_template.get("args", {}),
                    }
                    success, state = _execute_action(world, actual_action)
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {actual_action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=decision.action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                    trace.chosen_action = actual_action
                elif decision.action and decision.action.get("service") == "planner.select" and memory_ops:
                    current_grounding_ids = []
                    trace.chosen_action = None
                elif decision.action and decision.action.get("service") == "planner.select" and inferred_action:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:1]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    success, state = _execute_action(world, inferred_action)
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {inferred_action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=inferred_action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                    trace.chosen_action = inferred_action
                elif decision.action and decision.action.get("service") == "planner.select":
                    trace.should_ask_user = True
                    trace.clarification_turns += 1
                    if trace.safety_relevant:
                        trace.safety_gated = True
                    current_grounding_ids = []
                    trace.chosen_action = None
                elif decision.action:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:1]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    success, state = _execute_action(world, decision.action)
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {decision.action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=decision.action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                    trace.chosen_action = decision.action
                else:
                    current_grounding_ids = []
                    trace.chosen_action = decision.action
        elif step_type == "expect_action":
            asserted = step.get("assert", {})
            if asserted:
                trace.preferred_action = asserted
                trace.ground_truth_entity = asserted["entity_id"]
                expected_entity = asserted["entity_id"]
                if expected_entity in world.states:
                    state = world.get_state(expected_entity)
                    trace.ground_truth_state = {expected_entity: state}
                else:
                    trace.ground_truth_state = {}
                chosen = trace.chosen_action or {}
                if (
                    chosen.get("service") != asserted["service"]
                    or chosen.get("entity_id") != expected_entity
                    or chosen.get("args", {}) != asserted.get("args", {})
                ):
                    assertion_status["action"]["seen"] = True
                    assertion_status["action"]["success"] = False
                    _append_assertion_result(
                        trace,
                        kind="action",
                        step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        success=False,
                        expected=asserted,
                        observed=chosen,
                    )
                    assertion_failures.append(
                        f"expect_action mismatch for {step.get('step_id')}: got {chosen}, expected {asserted}"
                    )
                    for memory_id in current_grounding_ids:
                        trace.usage_events.append(
                            {
                                "memory_id": memory_id,
                                "used_stage": "planning",
                                "contribution": "misleading",
                                "outcome": "failure",
                                "note": f"{step.get('step_id')} action mismatch",
                            }
                        )
                else:
                    assertion_status["action"]["seen"] = True
                    _append_assertion_result(
                        trace,
                        kind="action",
                        step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        success=True,
                        expected=asserted,
                        observed=chosen,
                    )
                    for memory_id in current_grounding_ids:
                        trace.usage_events.append(
                            {
                                "memory_id": memory_id,
                                "used_stage": "planning",
                                "contribution": "helpful",
                                "outcome": "success",
                                "note": f"{step.get('step_id')} action matched",
                            }
                        )
        elif step_type == "expect_clarify":
            clarify_ok = bool(trace.should_ask_user and trace.clarification_turns >= 1)
            assertion_status["clarification"]["seen"] = True
            assertion_status["clarification"]["success"] = assertion_status["clarification"]["success"] and clarify_ok
            _append_assertion_result(
                trace,
                kind="clarification",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=clarify_ok,
                observed={
                    "should_ask_user": trace.should_ask_user,
                    "clarification_turns": trace.clarification_turns,
                },
            )
            if not clarify_ok:
                assertion_failures.append(
                    f"expect_clarify mismatch for {step.get('step_id')}: should_ask_user={trace.should_ask_user}, clarification_turns={trace.clarification_turns}"
                )
        elif step_type == "expect_no_action":
            no_action_ok = trace.chosen_action is None
            assertion_status["action"]["seen"] = True
            assertion_status["action"]["success"] = assertion_status["action"]["success"] and no_action_ok
            _append_assertion_result(
                trace,
                kind="action",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=no_action_ok,
                observed={
                    "chosen_action": trace.chosen_action,
                    "should_ask_user": trace.should_ask_user,
                },
            )
            if not no_action_ok:
                assertion_failures.append(
                    f"expect_no_action mismatch for {step.get('step_id')}: chosen_action={trace.chosen_action}"
                )
        elif step_type == "expect_final_state":
            expected_state = step.get("assert", {})
            trace.ground_truth_state = expected_state
            final_ok = trace.final_device_state == expected_state
            assertion_status["final_state"]["seen"] = True
            assertion_status["final_state"]["success"] = assertion_status["final_state"]["success"] and final_ok
            _append_assertion_result(
                trace,
                kind="final_state",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=final_ok,
                expected=expected_state,
                observed=trace.final_device_state,
            )
            if not final_ok:
                assertion_failures.append(
                    f"expect_final_state mismatch for {step.get('step_id')}: got {trace.final_device_state}, expected {expected_state}"
                )
        elif step_type == "expect_memory":
            selector = step.get("selector", {})
            records = service.store.list(include_deleted=True)
            matched = [
                record for record in records
                if all(getattr(record, key) == value for key, value in selector.items())
            ]
            memory_ok = bool(matched)
            if not matched:
                assertion_failures.append(f"expect_memory no match for selector {selector}")
            for record in matched:
                trace.memory_status_after[record.memory_id] = record.status
                for key, value in step.get("assert", {}).items():
                    if getattr(record, key) != value:
                        memory_ok = False
                        assertion_failures.append(
                            f"expect_memory mismatch {record.memory_id}.{key}: got {getattr(record, key)!r}, expected {value!r}"
                        )
            assertion_status["memory"]["seen"] = True
            assertion_status["memory"]["success"] = assertion_status["memory"]["success"] and memory_ok
            _append_assertion_result(
                trace,
                kind="memory",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=memory_ok,
                expected={
                    "selector": selector,
                    "assert": step.get("assert", {}),
                },
                observed=[
                    record.model_dump(mode="json")
                    for record in matched
                ],
            )
        elif step_type == "expect_absent_memory":
            selector = step.get("selector", {})
            records = service.store.list(include_deleted=True)
            matched = [
                record for record in records
                if all(getattr(record, key) == value for key, value in selector.items())
                and record.status != "deleted"
            ]
            memory_ok = not matched
            assertion_status["memory"]["seen"] = True
            assertion_status["memory"]["success"] = assertion_status["memory"]["success"] and memory_ok
            _append_assertion_result(
                trace,
                kind="memory",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=memory_ok,
                expected={"selector": selector, "absent": True},
                observed=[record.model_dump(mode="json") for record in matched],
            )
            if matched:
                assertion_failures.append(
                    f"expect_absent_memory mismatch for selector {selector}: found {[record.memory_id for record in matched]}"
                )

    for event in trace.usage_events:
        memory_id = event.get("memory_id")
        if memory_id and "used_stage" in event and "contribution" in event and "outcome" in event:
            service.mark_outcome(
                UsageEvent(
                    task_id=trace.task_id,
                    memory_id=memory_id,
                    used_stage=event["used_stage"],
                    contribution=event["contribution"],
                    outcome=event["outcome"],
                    note=event["note"],
                    timestamp=world.current_time,
                )
            )

    final_records = service.list_records(include_deleted=True)
    for record in final_records:
        trace.memory_status_after[record.memory_id] = record.status
    trace.memory_records_after = [record.model_dump(mode="json") for record in final_records]
    if trace.maintenance_events:
        trace.maintenance_latency_ms = sum(item.maintenance_latency_ms for item in trace.maintenance_events)
        trace.estimated_maintenance_tokens = sum(
            item.estimated_maintenance_tokens for item in trace.maintenance_events
        )
    trace.action_success = assertion_status["action"]["success"] if assertion_status["action"]["seen"] else None
    trace.clarification_success = (
        assertion_status["clarification"]["success"] if assertion_status["clarification"]["seen"] else None
    )
    trace.memory_assertion_success = assertion_status["memory"]["success"] if assertion_status["memory"]["seen"] else None
    trace.final_state_success = (
        assertion_status["final_state"]["success"] if assertion_status["final_state"]["seen"] else None
    )
    trace.task_success = not assertion_failures
    trace.outcome = "success" if trace.task_success else "failure"
    if assertion_failures:
        trace.usage_events.append(
            {
                "kind": "assertion_failures",
                "query": last_query,
                "failures": assertion_failures,
                "task_type": last_task_type,
            }
        )
    trace.end_to_end_latency_ms = (time.perf_counter() - started) * 1000
    return trace.model_dump(mode="json")


def run_agent_scenario(
    scenario: dict,
    *,
    seed: int = 1001,
    results_root: str | Path | None = None,
    system_config: SystemConfig | None = None,
) -> dict:
    started = time.perf_counter()
    world = HAOracle()
    world.reset()
    if scenario.get("initial_state_overrides"):
        for entity_id, override in scenario["initial_state_overrides"].items():
            world.states[entity_id] = override

    temp_db = Path(tempfile.gettempdir()) / f"{scenario['scenario_id']}_{seed}_agent_{uuid.uuid4().hex}.sqlite3"
    system_config = system_config or SystemConfig(system_id="Ours", planner_mode="agent")
    service = MemoryService(temp_db, config=system_config.__dict__)
    adapter = AgentAdapter()

    trace = TaskTrace(
        task_id=f"{scenario['scenario_id']}_{seed}",
        scenario_id=scenario["scenario_id"],
        seed=seed,
        world_version=scenario["world_version"],
        system_policy_version=scenario.get("system_policy_version", "sp-v1"),
        planner_mode="agent",
        system_id=system_config.system_id,
        task_type=scenario.get("task_type", "control"),
        sim_time=world.current_time,
        safety_relevant=bool(scenario.get("safety_relevant", False)),
    )
    assertion_failures: list[str] = []
    current_grounding_ids: list[str] = []
    assertion_status = {
        "action": {"seen": False, "success": True},
        "clarification": {"seen": False, "success": True},
        "memory": {"seen": False, "success": True},
        "final_state": {"seen": False, "success": True},
    }

    _load_fixture_records(service, scenario.get("initial_memory_fixture", []), world.current_time)

    for index, step in enumerate(scenario.get("steps", []), start=1):
        step_type = step["type"]
        sim_time = _parse_sim_time(world.t0, step.get("sim_time"))
        if sim_time > world.current_time:
            world.advance_to(sim_time)
        trace.sim_time = world.current_time

        if step_type == "event":
            event_id = step.get("event_id")
            if event_id:
                event = next(
                    item for item in world.definition["events"] if item["event_id"] == event_id
                )
                event_time = _parse_sim_time(world.t0, step["sim_time"])
                world.apply_event({**event, "sim_time": event_time.isoformat()})
            else:
                before = {item.memory_id: item.status for item in service.list_records(include_deleted=True)}
                maintenance_result = _apply_custom_event(world, service, step, world.current_time)
                after = {item.memory_id: item.status for item in service.list_records(include_deleted=True)}
                changed = [memory_id for memory_id, status in after.items() if before.get(memory_id) != status]
                if changed or maintenance_result:
                    trace.usage_events.append({"kind": "maintenance", "changed_memory_ids": changed})
                if maintenance_result:
                    for memory_id in maintenance_result["resampled_memory_ids"]:
                        trace.usage_events.append({"kind": "resampled", "memory_id": memory_id})
                    trace.maintenance_events.append(
                        MaintenanceTrace(
                            maintenance_id=step.get("step_id", f"mnt_{index}"),
                            sim_time=world.current_time,
                            expired_memory_ids=maintenance_result["expired_memory_ids"],
                            stale_memory_ids=maintenance_result["stale_memory_ids"],
                            archived_memory_ids=maintenance_result["archived_memory_ids"],
                            resampled_memory_ids=maintenance_result["resampled_memory_ids"],
                            rollback_merge_ids=maintenance_result["rollback_merge_ids"],
                            needs_review_ids=maintenance_result.get("needs_review_ids", []),
                            deleted_by_capacity_ids=maintenance_result.get("deleted_by_capacity_ids", []),
                            maintenance_latency_ms=maintenance_result.get("maintenance_latency_ms", 0.0),
                            estimated_maintenance_tokens=maintenance_result.get("estimated_maintenance_tokens", 0),
                        )
                    )
            continue

        if step_type == "say":
            oracle_input = step.get("oracle_input", {})
            for op in oracle_input.get("memory_ops", []):
                service.apply_memory_op(op, world.current_time)
            query = step.get("text", "")
            package = service.search(
                query,
                task_type=oracle_input.get("task_type", scenario.get("task_type", "control")),
                now=world.current_time,
            )
            package = _inject_registry_candidates(world, package, query)
            trace.estimated_prompt_tokens += _approx_tokens(query) + sum(
                _approx_tokens(item.text) for item in package.matched_memories
            )
            retrieval_trace = RetrievalStepTrace(
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                stage="planning",
                query=query,
                retrieved_memories=[
                    RetrievedMemoryTrace(
                        memory_id=item.memory_id,
                        memory_type=item.memory_type,
                        rank=rank,
                        retrieval_score=item.score,
                        effective_confidence=item.effective_confidence,
                        memory_worth=item.memory_worth,
                        raw_confidence=item.raw_confidence,
                        system_status=item.system_status,
                        true_status=item.true_status,
                        runtime_status=item.runtime_status,
                        in_usable_set=item.in_usable_set,
                        in_grounding_set=False,
                    )
                    for rank, item in enumerate(package.matched_memories, start=1)
                ],
            )
            trace.steps.append(retrieval_trace)
            decision = adapter.plan(package, query)
            trace.agent_backend = decision.backend
            trace.should_ask_user = decision.should_ask_user
            if decision.model:
                trace.agent_model = decision.model
            if decision.provider:
                trace.agent_provider = decision.provider
            action_template = oracle_input.get("action_template")
            memory_ops = oracle_input.get("memory_ops", [])
            inferred_action = None
            if not action_template and not memory_ops:
                inferred_action = _infer_control_action(query, package, world)
            if decision.raw_output:
                trace.agent_raw_outputs.append(decision.raw_output)
                trace.usage_events.append({"kind": "agent_output", "text": decision.raw_output})
            if decision.structured_output:
                trace.agent_structured_decisions.append(
                    {
                        "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        **decision.structured_output,
                    }
                )
            if decision.tool_calls:
                trace.agent_tool_calls.append(
                    {
                        "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        "tool_calls": decision.tool_calls,
                    }
                )
            if decision.usage:
                trace.agent_usage_metadata.append(
                    {
                        "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        **decision.usage,
                    }
                )
            if decision.latency_ms:
                trace.agent_latencies_ms.append(decision.latency_ms)
            if decision.failure_type:
                trace.agent_failures.append(decision.failure_type)
            planned_actions = list(decision.actions or ([decision.action] if decision.action else []))
            trace.chosen_actions = planned_actions
            if decision.should_ask_user:
                trace.clarification_turns += 1
                if trace.safety_relevant:
                    trace.safety_gated = True
                current_grounding_ids = []
                trace.chosen_action = None
                trace.chosen_actions = []
            else:
                if decision.action and decision.action.get("service") == "planner.select" and action_template:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:1]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    actual_action = {
                        "service": action_template["service"],
                        "entity_id": decision.action["entity_id"],
                        "args": action_template.get("args", {}),
                    }
                    success, state = _execute_action(world, actual_action)
                    trace.action_execution_results.append(
                        {
                            "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                            "action": actual_action,
                            "success": success,
                            "result_state": state,
                        }
                    )
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {actual_action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=decision.action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                    trace.chosen_action = actual_action
                    trace.chosen_actions = [actual_action]
                elif decision.action and decision.action.get("service") == "planner.select" and memory_ops:
                    current_grounding_ids = []
                    trace.chosen_action = None
                    trace.chosen_actions = []
                elif decision.action and decision.action.get("service") == "planner.select" and inferred_action:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:1]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    success, state = _execute_action(world, inferred_action)
                    trace.action_execution_results.append(
                        {
                            "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                            "action": inferred_action,
                            "success": success,
                            "result_state": state,
                        }
                    )
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {inferred_action}")
                        _write_failure_reflection(
                            service,
                            task_id=trace.task_id,
                            query=query,
                            entity_id=inferred_action["entity_id"],
                            now=world.current_time,
                            reason="execution_failure",
                        )
                    trace.chosen_action = inferred_action
                    trace.chosen_actions = [inferred_action]
                elif decision.action and decision.action.get("service") == "planner.select":
                    trace.should_ask_user = True
                    trace.clarification_turns += 1
                    if trace.safety_relevant:
                        trace.safety_gated = True
                    current_grounding_ids = []
                    trace.chosen_action = None
                    trace.chosen_actions = []
                elif planned_actions:
                    current_grounding_ids = [
                        item.memory_id
                        for item in package.matched_memories
                        if item.in_usable_set
                    ][:3]
                    for item in retrieval_trace.retrieved_memories:
                        if item.memory_id in current_grounding_ids:
                            item.in_grounding_set = True
                    success, state, execution_results = _execute_actions(world, planned_actions)
                    for result in execution_results:
                        trace.action_execution_results.append(
                            {
                                "step_id": step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                                **result,
                            }
                        )
                    if success:
                        trace.final_device_state.update(state)
                    else:
                        assertion_failures.append(f"world.apply failed: {planned_actions}")
                        for result in execution_results:
                            if result["success"]:
                                continue
                            _write_failure_reflection(
                                service,
                                task_id=trace.task_id,
                                query=query,
                                entity_id=result["action"].get("entity_id"),
                                now=world.current_time,
                                reason="execution_failure",
                            )
                    trace.chosen_action = planned_actions[0]
                    trace.chosen_actions = planned_actions
                else:
                    current_grounding_ids = []
                    trace.chosen_action = decision.action
                    trace.chosen_actions = planned_actions
        elif step_type == "expect_action":
            asserted = step.get("assert", {})
            if asserted:
                trace.preferred_action = asserted
                trace.ground_truth_entity = asserted["entity_id"]
                expected_entity = asserted["entity_id"]
                if expected_entity in world.states:
                    state = world.get_state(expected_entity)
                    trace.ground_truth_state = {expected_entity: state}
                else:
                    trace.ground_truth_state = {}
                chosen = trace.chosen_action or {}
                if (
                    chosen.get("service") != asserted["service"]
                    or chosen.get("entity_id") != expected_entity
                    or chosen.get("args", {}) != asserted.get("args", {})
                ):
                    assertion_status["action"]["seen"] = True
                    assertion_status["action"]["success"] = False
                    _append_assertion_result(
                        trace,
                        kind="action",
                        step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        success=False,
                        expected=asserted,
                        observed=chosen,
                    )
                    assertion_failures.append(
                        f"expect_action mismatch for {step.get('step_id')}: got {chosen}, expected {asserted}"
                    )
                    for memory_id in current_grounding_ids:
                        trace.usage_events.append(
                            {
                                "memory_id": memory_id,
                                "used_stage": "planning",
                                "contribution": "misleading",
                                "outcome": "failure",
                                "note": f"{step.get('step_id')} action mismatch",
                            }
                        )
                else:
                    assertion_status["action"]["seen"] = True
                    _append_assertion_result(
                        trace,
                        kind="action",
                        step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                        success=True,
                        expected=asserted,
                        observed=chosen,
                    )
                    for memory_id in current_grounding_ids:
                        trace.usage_events.append(
                            {
                                "memory_id": memory_id,
                                "used_stage": "planning",
                                "contribution": "helpful",
                                "outcome": "success",
                                "note": f"{step.get('step_id')} action matched",
                            }
                        )
        elif step_type == "expect_clarify":
            clarify_ok = bool(trace.should_ask_user and trace.clarification_turns >= 1)
            assertion_status["clarification"]["seen"] = True
            assertion_status["clarification"]["success"] = assertion_status["clarification"]["success"] and clarify_ok
            _append_assertion_result(
                trace,
                kind="clarification",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=clarify_ok,
                observed={
                    "should_ask_user": trace.should_ask_user,
                    "clarification_turns": trace.clarification_turns,
                },
            )
            if not clarify_ok:
                assertion_failures.append(
                    f"expect_clarify mismatch for {step.get('step_id')}: should_ask_user={trace.should_ask_user}, clarification_turns={trace.clarification_turns}"
                )
        elif step_type == "expect_no_action":
            no_action_ok = trace.chosen_action is None
            assertion_status["action"]["seen"] = True
            assertion_status["action"]["success"] = assertion_status["action"]["success"] and no_action_ok
            _append_assertion_result(
                trace,
                kind="action",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=no_action_ok,
                observed={
                    "chosen_action": trace.chosen_action,
                    "should_ask_user": trace.should_ask_user,
                },
            )
            if not no_action_ok:
                assertion_failures.append(
                    f"expect_no_action mismatch for {step.get('step_id')}: chosen_action={trace.chosen_action}"
                )
        elif step_type == "expect_final_state":
            expected_state = step.get("assert", {})
            trace.ground_truth_state = expected_state
            final_ok = trace.final_device_state == expected_state
            assertion_status["final_state"]["seen"] = True
            assertion_status["final_state"]["success"] = assertion_status["final_state"]["success"] and final_ok
            _append_assertion_result(
                trace,
                kind="final_state",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=final_ok,
                expected=expected_state,
                observed=trace.final_device_state,
            )
            if not final_ok:
                assertion_failures.append(
                    f"expect_final_state mismatch for {step.get('step_id')}: got {trace.final_device_state}, expected {expected_state}"
                )
        elif step_type == "expect_memory":
            selector = step.get("selector", {})
            records = service.store.list(include_deleted=True)
            matched = [
                record for record in records
                if all(getattr(record, key) == value for key, value in selector.items())
            ]
            memory_ok = bool(matched)
            if not matched:
                assertion_failures.append(f"expect_memory no match for selector {selector}")
            for record in matched:
                trace.memory_status_after[record.memory_id] = record.status
                for key, value in step.get("assert", {}).items():
                    if getattr(record, key) != value:
                        memory_ok = False
                        assertion_failures.append(
                            f"expect_memory mismatch {record.memory_id}.{key}: got {getattr(record, key)!r}, expected {value!r}"
                        )
            assertion_status["memory"]["seen"] = True
            assertion_status["memory"]["success"] = assertion_status["memory"]["success"] and memory_ok
            _append_assertion_result(
                trace,
                kind="memory",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=memory_ok,
                expected={
                    "selector": selector,
                    "assert": step.get("assert", {}),
                },
                observed=[
                    record.model_dump(mode="json")
                    for record in matched
                ],
            )
        elif step_type == "expect_absent_memory":
            selector = step.get("selector", {})
            records = service.store.list(include_deleted=True)
            matched = [
                record for record in records
                if all(getattr(record, key) == value for key, value in selector.items())
                and record.status != "deleted"
            ]
            memory_ok = not matched
            assertion_status["memory"]["seen"] = True
            assertion_status["memory"]["success"] = assertion_status["memory"]["success"] and memory_ok
            _append_assertion_result(
                trace,
                kind="memory",
                step_id=step.get("step_id", f"{scenario['scenario_id']}_{index}"),
                success=memory_ok,
                expected={"selector": selector, "absent": True},
                observed=[record.model_dump(mode="json") for record in matched],
            )
            if matched:
                assertion_failures.append(
                    f"expect_absent_memory mismatch for selector {selector}: found {[record.memory_id for record in matched]}"
                )

    for event in trace.usage_events:
        memory_id = event.get("memory_id")
        if memory_id and "used_stage" in event and "contribution" in event and "outcome" in event:
            service.mark_outcome(
                UsageEvent(
                    task_id=trace.task_id,
                    memory_id=memory_id,
                    used_stage=event["used_stage"],
                    contribution=event["contribution"],
                    outcome=event["outcome"],
                    note=event["note"],
                    timestamp=world.current_time,
                )
            )

    final_records = service.list_records(include_deleted=True)
    for record in final_records:
        trace.memory_status_after[record.memory_id] = record.status
    trace.memory_records_after = [record.model_dump(mode="json") for record in final_records]
    if trace.maintenance_events:
        trace.maintenance_latency_ms = sum(item.maintenance_latency_ms for item in trace.maintenance_events)
        trace.estimated_maintenance_tokens = sum(
            item.estimated_maintenance_tokens for item in trace.maintenance_events
        )
    trace.action_success = assertion_status["action"]["success"] if assertion_status["action"]["seen"] else None
    trace.clarification_success = (
        assertion_status["clarification"]["success"] if assertion_status["clarification"]["seen"] else None
    )
    trace.memory_assertion_success = assertion_status["memory"]["success"] if assertion_status["memory"]["seen"] else None
    trace.final_state_success = (
        assertion_status["final_state"]["success"] if assertion_status["final_state"]["seen"] else None
    )
    trace.task_success = not assertion_failures
    trace.outcome = "success" if trace.task_success else "failure"
    if assertion_failures:
        trace.usage_events.append({"kind": "assertion_failures", "failures": assertion_failures})
    trace.end_to_end_latency_ms = (time.perf_counter() - started) * 1000
    return trace.model_dump(mode="json")
