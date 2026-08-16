from __future__ import annotations

import json
import io
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
import subprocess
import sys
from unittest import mock

from experiments.memory.schemas import CandidateDevice, MatchedMemory, SearchResultPackage, UsageEvent
from experiments.memory.service import MemoryService
from experiments.memory.text_ingestion import ingest_user_text
from experiments.metrics.core import aggregate_task_metrics, task_metrics
from experiments.evaluator.lifecycle import evaluator_status_for_record
from experiments.evaluator.protocol import validate_v4_agent_scenario
from experiments.runner.system_registry import SystemConfig, build_system_registry
from experiments.scripts.audit_mechanism_activation import audit_activation
from experiments.scripts.audit_protocol_v4_preflight import audit as audit_v4_preflight
from experiments.scripts.audit_protocol_v4_readiness import audit as audit_v4_readiness
from experiments.scripts.audit_llm_assisted_annotation import audit as audit_llm_assisted_annotation
from experiments.scripts.analyze_protocol_v4_formal import (
    METRIC_SPECS,
    collect_traces,
    guard_diagnostics,
    holm_adjust,
    mcnemar_exact,
    paired_sign_flip_p_value,
    two_way_cluster_bootstrap,
)
from experiments.scripts.compute_annotation_agreement import compute as compute_annotation_agreement
from experiments.scripts.finalize_protocol_v4_formal import _write_csv, _write_markdown
from experiments.planners.agent_planner import AgentPlanner, AgentPlannerDecision, ExternalLLMClient, _build_plan_prompt
from experiments.runner.batch_run import run_batch, run_batch_multi_seed
from experiments.runner.scenario_loader import load_scenario
from experiments.runner.single_run import _infer_control_action, run_agent_scenario
from experiments.world_model.ha_oracle import HAOracle


class WorldModelTest(unittest.TestCase):
    def test_product_runtime_audit_records_response_usage_and_tool_calls(self):
        import importlib

        runtime_v1 = importlib.import_module("smartHome.m_agent.memory.runtime_v1")
        runtime = runtime_v1.DemoMemoryRuntime.__new__(runtime_v1.DemoMemoryRuntime)
        runtime.current_task = runtime_v1.DemoTaskContext(task_id="task-test", task="测试任务")
        runtime.completed_task_audits = []
        message = type(
            "Message",
            (),
            {
                "usage_metadata": {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18},
                "response_metadata": {"model_name": "stub-model", "model_provider": "stub-provider"},
                "tool_calls": [{"name": "query_tool", "args": {"query": "偏好"}}],
            },
        )()
        runtime.record_llm_response(message)
        task = runtime.current_task
        self.assertEqual(task.llm_responses[0]["usage"]["total_tokens"], 18)
        self.assertEqual(task.tool_calls[0]["name"], "query_tool")

    def test_product_runtime_retries_only_upstream_unavailable_once(self):
        from smartHome.m_agent.agent.hooks.langchain_middleware import retry_upstream_unavailable

        attempts = []
        def handler(_request):
            attempts.append("call")
            if len(attempts) == 1:
                raise RuntimeError("upstream_unavailable")
            return "ok"
        with mock.patch("smartHome.m_agent.agent.hooks.langchain_middleware.time.sleep"):
            result = retry_upstream_unavailable.wrap_model_call(None, handler)
        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 2)

    def test_product_runtime_does_not_retry_non_transport_error(self):
        from smartHome.m_agent.agent.hooks.langchain_middleware import retry_upstream_unavailable

        with self.assertRaisesRegex(RuntimeError, "invalid request"):
            retry_upstream_unavailable.wrap_model_call(None, lambda _request: (_ for _ in ()).throw(RuntimeError("invalid request")))

    def test_product_runtime_retries_only_known_proxy_timeout_or_channel_failure(self):
        from smartHome.m_agent.agent.hooks.langchain_middleware import _is_retryable_upstream_unavailable

        self.assertTrue(_is_retryable_upstream_unavailable(RuntimeError("APITimeoutError: Request timed out.")))
        self.assertTrue(_is_retryable_upstream_unavailable(RuntimeError("500 get_channel_failed: 可用渠道不存在")))
        self.assertFalse(_is_retryable_upstream_unavailable(RuntimeError("500 invalid output schema")))

    def test_product_llm_uses_explicit_timeout_and_no_sdk_retries(self):
        from smartHome.m_agent.common import get_llm

        captured = {}
        def fake_init_chat_model(**kwargs):
            captured.update(kwargs)
            return object()
        with mock.patch.object(get_llm, "init_chat_model", side_effect=fake_init_chat_model):
            get_llm.get_llm()
        self.assertEqual(captured["max_retries"], 0)
        self.assertEqual(captured["timeout"], 90)

    def test_product_runtime_runner_records_isolated_provider_override(self):
        import importlib.util

        path = Path("experiments/scripts/run_v4_2_product_runtime.py")
        spec = importlib.util.spec_from_file_location("product_runtime_runner", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        row = module._base_row(
            {"trajectory_id": "R1", "utterances": ["偏好"], "task": "执行", "coverage": "create"},
            1001,
            Path("/tmp/runtime.sqlite3"),
            "my_gac",
        )
        self.assertEqual(row["configured_provider"], "my_gac")

    def test_v42_preregistration_covers_all_supplemental_requirements(self):
        config = json.loads(Path("experiments/configs/protocol_v4_2_supplemental.json").read_text(encoding="utf-8"))
        self.assertEqual(len(config["product_runtime_trajectories"]), 3)
        self.assertEqual(len(config["mechanism_trajectories"]), 8)
        self.assertEqual(len(config["longitudinal_trajectories"]), 5)
        self.assertEqual(config["replicate_ids"], list(range(1001, 1011)))
        self.assertEqual(set(config["longitudinal_systems"]), {"Ours", "B0", "B1", "B2", "B3", "B4", "B5"})

    def test_v42_extractor_prompt_requires_memory_service_operation_key(self):
        from experiments.scripts.run_protocol_v42_supplemental import _extract_prompt

        prompt = _extract_prompt({"utterances": ["请记住我的偏好"]})
        self.assertIn("键名 op", prompt)
        self.assertNotIn("operation 可为", prompt)

    def test_v42_extractor_prompt_declares_memory_record_enum_constraints(self):
        from experiments.scripts.run_protocol_v42_supplemental import _extract_prompt

        prompt = _extract_prompt({"utterances": ["请记住我的偏好"]})
        self.assertIn("source 只能为 user_explicit 或 user_correction", prompt)
        self.assertIn("object 必须为字符串", prompt)

    def test_v42_extractor_prompt_exposes_existing_memory_context_per_turn(self):
        from experiments.scripts.run_protocol_v42_supplemental import _extract_prompt

        prompt = _extract_prompt(
            {"utterances": ["更正这个偏好"]},
            turn_index=1,
            records=[{"memory_id": "memory_1", "natural_text": "卧室空调偏好24度"}],
        )
        self.assertIn("memory_1", prompt)
        self.assertIn("当前第 1 轮", prompt)

    def test_v42_runner_persists_runner_exceptions(self):
        source = Path("experiments/scripts/run_protocol_v42_supplemental.py").read_text(encoding="utf-8")
        self.assertIn('"agent_backend": "runner_exception"', source)
        self.assertIn('"runner_traceback": traceback.format_exc', source)

    def test_v42_longitudinal_runner_has_bounded_transport_repair(self):
        source = Path("experiments/scripts/run_protocol_v42_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("for attempt_number in (1, 2)", source)
        self.assertIn('"transport_repair": len(attempts) > 1', source)

    def test_v42_longitudinal_analysis_reports_tsr_wdr_and_clustered_ci(self):
        source = Path("experiments/scripts/analyze_v4_2_longitudinal.py").read_text(encoding="utf-8")
        self.assertIn('"tsr"', source)
        self.assertIn('"wdr"', source)
        self.assertIn("two_way_cluster_bootstrap", source)

    def test_apply_light_turn_on(self):
        world = HAOracle()
        result = world.apply("light.turn_on", {"entity": "light.study_desk"}, world.current_time)
        self.assertTrue(result["success"])
        self.assertEqual(world.get_state("light.study_desk")["state"], "on")

    def test_scheduled_event_removes_device(self):
        world = HAOracle()
        target = world.t0 + timedelta(days=5, hours=9)
        world.advance_to(target)
        self.assertNotIn("switch.study_heater", world.entities)

    def test_apply_rejects_missing_unsupported_and_out_of_range_actions(self):
        world = HAOracle()
        self.assertEqual(
            world.apply("climate.set_temperature", {"entity": "climate.bedroom_ac"})["error"],
            "missing_required_args",
        )
        self.assertEqual(
            world.apply("light.turn_on", {"entity": "light.bedroom_bedside", "color_temp": 300})["error"],
            "unsupported_capability",
        )
        self.assertEqual(
            world.apply("cover.set_position", {"entity": "cover.living_curtain", "position": 101})["error"],
            "argument_out_of_range",
        )
        self.assertEqual(
            world.apply("climate.set_temperature", {"entity": "climate.bedroom_ac", "temperature": 31})["error"],
            "argument_out_of_range",
        )
        self.assertEqual(
            world.apply("light.turn_on", {"entity": "light.study_desk", "brightness": -1})["error"],
            "argument_out_of_range",
        )

    def test_apply_rejects_wrong_device_domain_and_unknown_service(self):
        world = HAOracle()
        self.assertEqual(
            world.apply("lock.unlock", {"entity": "light.study_desk"})["error"],
            "wrong_domain",
        )
        self.assertEqual(
            world.apply("lock.open", {"entity": "lock.front_door"})["error"],
            "unsupported_service",
        )


class MemoryServiceTest(unittest.TestCase):
    def test_evaluator_lifecycle_truth_does_not_copy_system_status(self):
        now = HAOracle().current_time
        record = {
            "status": "active",
            "valid_until": (now - timedelta(minutes=1)).isoformat(),
            "structured_payload": {},
        }
        self.assertEqual(evaluator_status_for_record(record, now), "expired")

    def test_v4_rejects_gold_memory_and_action_bridges(self):
        scenario = {
            "steps": [{
                "step_id": "s1",
                "type": "say",
                "text": "打开灯",
                "oracle_input": {
                    "memory_ops": [{"op": "add_active"}],
                    "action_template": {"service": "light.turn_on"},
                },
            }]
        }
        violations = validate_v4_agent_scenario(scenario)
        self.assertIn("s1:gold_memory_ops", violations)
        self.assertIn("s1:action_template", violations)

    def test_all_protocol_v4_scenarios_have_no_hidden_evaluator_bridge(self):
        scenario_root = Path("experiments/scenarios/protocol_v4")
        scenario_paths = sorted(scenario_root.glob("*.yaml"))
        self.assertGreaterEqual(len(scenario_paths), 12)
        violations = []
        for path in scenario_paths:
            scenario = load_scenario(path)
            violations.extend(validate_v4_agent_scenario(scenario))
        self.assertEqual(violations, [])

    def test_expanded_v4_behavioral_and_second_world_assets_are_explicit(self):
        from experiments.scripts.build_protocol_v4_formal_assets import BEHAVIORAL_SCENARIOS, ROBUSTNESS_SCENARIOS

        self.assertEqual(len(BEHAVIORAL_SCENARIOS), 10)
        self.assertIn("B6W_v4_robustness", ROBUSTNESS_SCENARIOS)
        second_world = load_scenario(Path("experiments/scenarios/protocol_v4/B6W.yaml"))
        self.assertEqual(second_world["world_path"], "experiments/world_model/v2.json")
        self.assertEqual(second_world["world_version"], "wm-v2-alt-home")

    def test_evaluator_only_labels_are_not_serialized_into_agent_prompt(self):
        package = SearchResultPackage(
            query="打开睡前灯",
            task_type="control",
            matched_memories=[],
            candidate_devices=[CandidateDevice(entity_id="light.study_desk", name="台灯", score=1.0, confidence=1.0)],
        )
        prompt = _build_plan_prompt("打开睡前灯", package)
        for forbidden in (
            "evaluator_preferred_action",
            "evaluator_safety_gate_required",
            "evaluator_correction_pairs",
            "evaluator_status",
        ):
            self.assertNotIn(forbidden, prompt)

    def test_upsert_and_search_alias(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_test.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_type": "alias",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
            },
            now,
        )
        result = service.search("打开小书灯", task_type="control", now=now)
        self.assertTrue(result.matched_memories)
        self.assertEqual(result.matched_memories[0].memory_type, "alias")

    def test_dead_memory_resample_and_archive(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_dead_memory.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "mem_fuzzy_alias",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "那个灯",
                "structured_payload": {"resampled_text": "小书灯指的是书房台灯 light.study_desk"},
            },
            now,
        )
        service.maintenance(now + timedelta(days=400))
        fuzzy = service.get("mem_fuzzy_alias")
        self.assertTrue(fuzzy.resampled)
        self.assertEqual(fuzzy.status, "active")
        self.assertEqual(fuzzy.structured_payload["resampled_from"], "那个灯")
        self.assertIn("resampled_at", fuzzy.structured_payload)

        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "mem_specific_alias",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "客厅落地灯",
                "predicate": "refers_to",
                "object": "light.living_floor",
                "entity_id": "light.living_floor",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "客厅落地灯就是客厅落地灯",
            },
            now,
        )
        service.maintenance(now + timedelta(days=400))
        specific = service.get("mem_specific_alias")
        self.assertEqual(specific.status, "archived")

    def test_sensitive_capacity_governance_marks_needs_review(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_capacity.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path, config={"active_target_limit": 1, "dormant_target_limit": 0, "archived_target_limit": 0})
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "cap_sensitive_a",
                "memory_type": "preference",
                "scope": "entity",
                "subject": "敏感偏好A",
                "predicate": "preferred_temperature",
                "object": "25",
                "entity_id": "climate.bedroom_ac",
                "source": "user_explicit",
                "half_life_days": 180,
                "natural_text": "敏感偏好A",
                "sensitive": True,
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "cap_sensitive_b",
                "memory_type": "preference",
                "scope": "entity",
                "subject": "敏感偏好B",
                "predicate": "preferred_temperature",
                "object": "26",
                "entity_id": "climate.bedroom_ac",
                "source": "user_explicit",
                "half_life_days": 180,
                "natural_text": "敏感偏好B",
                "sensitive": True,
            },
            now,
        )
        result = service.maintenance(now)
        records = {item.memory_id: item for item in service.list_records(include_deleted=True)}
        self.assertTrue(any(item.needs_review for item in records.values()))
        self.assertIn("needs_review_ids", result)

    def test_stale_memory_is_query_usable_but_control_clarification_only(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_stale_clarify.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "stale_alias_memory",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "confidence": 0.90,
                "natural_text": "小书灯就是书房台灯",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "patch",
                "memory_id": "stale_alias_memory",
                "updates": {"status": "stale"},
            },
            now,
        )

        control_result = service.search("打开小书灯", task_type="control", now=now)
        control_match = next(
            item for item in control_result.matched_memories
            if item.memory_id == "stale_alias_memory"
        )
        self.assertEqual(control_match.runtime_status, "usable-stale")
        self.assertFalse(control_match.in_usable_set)
        self.assertTrue(control_result.should_ask_user)
        self.assertIn("stale_alias_memory", control_result.retrieval_metadata["clarification_only_memory_ids"])

        query_result = service.search("小书灯是什么", task_type="query", now=now)
        query_match = next(
            item for item in query_result.matched_memories
            if item.memory_id == "stale_alias_memory"
        )
        self.assertEqual(query_match.runtime_status, "usable-stale")
        self.assertTrue(query_match.in_usable_set)

    def test_helpful_success_reactivates_stale_memory(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_stale_reactivate.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "reactivate_alias_memory",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "confidence": 0.90,
                "natural_text": "小书灯就是书房台灯",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "patch",
                "memory_id": "reactivate_alias_memory",
                "updates": {"status": "stale"},
            },
            now,
        )

        service.mark_outcome(
            UsageEvent(
                task_id="reactivate_task",
                memory_id="reactivate_alias_memory",
                used_stage="verification",
                contribution="helpful",
                outcome="success",
                note="Saturday, July 25, 2026 verification recovered the stale alias",
                timestamp=now,
            )
        )

        record = service.get("reactivate_alias_memory")
        self.assertEqual(record.status, "active")
        self.assertEqual(record.layer, "active")
        self.assertEqual(record.positive_hits, 1)

    def test_negative_ripple_propagation_respects_distance(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_ripple.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "ripple_root",
                "memory_type": "episode",
                "scope": "entity",
                "subject": "门锁任务",
                "predicate": "uses",
                "object": "lock.front_door",
                "entity_id": "lock.front_door",
                "source": "user_explicit",
                "half_life_days": 30,
                "natural_text": "门锁任务执行 episode",
                "related_memory_ids": ["ripple_d1"],
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "ripple_d1",
                "memory_type": "location",
                "scope": "entity",
                "subject": "大门门锁",
                "predicate": "located_in",
                "object": "玄关",
                "entity_id": "lock.front_door",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "大门门锁在玄关",
                "related_memory_ids": ["ripple_d2"],
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "ripple_d2",
                "memory_type": "layout_relation",
                "scope": "room",
                "subject": "玄关布局",
                "predicate": "contains",
                "object": "lock.front_door",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "玄关布局包含门锁",
                "related_memory_ids": ["ripple_d3"],
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "ripple_d3",
                "memory_type": "episode",
                "scope": "entity",
                "subject": "门锁旁路节点",
                "predicate": "related_to",
                "object": "lock.front_door",
                "entity_id": "lock.front_door",
                "source": "user_explicit",
                "half_life_days": 30,
                "natural_text": "门锁旁路节点",
            },
            now,
        )

        service.apply_memory_op(
            {
                "op": "mark_outcome",
                "memory_id": "ripple_root",
                "used_stage": "verification",
                "contribution": "misleading",
                "outcome": "failure",
                "note": "lock episode failed",
            },
            now,
        )

        root = service.get("ripple_root")
        d1 = service.get("ripple_d1")
        d2 = service.get("ripple_d2")
        d3 = service.get("ripple_d3")
        self.assertEqual(root.ripple_penalty, 1.0)
        self.assertEqual(d1.ripple_penalty, 0.3)
        self.assertEqual(d2.ripple_penalty, 0.09)
        self.assertEqual(d3.ripple_penalty, 0.0)
        self.assertEqual(root.negative_hits, 1)
        self.assertEqual(d1.negative_hits, 1)
        self.assertEqual(d2.negative_hits, 1)
        self.assertEqual(d3.negative_hits, 0)

    def test_split_creates_lineage_and_edges(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_split.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "split_parent",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "客厅灯",
                "predicate": "refers_to",
                "object": "light.living_ceiling|light.living_ambient|light.living_floor",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "客厅灯泛指多盏灯",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "split",
                "old_memory_id": "split_parent",
                "new_records": [
                    {
                        "memory_id": "split_child_a",
                        "memory_type": "alias",
                        "scope": "entity",
                        "subject": "客厅顶灯",
                        "predicate": "refers_to",
                        "object": "light.living_ceiling",
                        "entity_id": "light.living_ceiling",
                        "source": "user_explicit",
                        "half_life_days": 365,
                        "natural_text": "客厅顶灯指客厅顶灯",
                    },
                    {
                        "memory_id": "split_child_b",
                        "memory_type": "alias",
                        "scope": "entity",
                        "subject": "客厅氛围灯",
                        "predicate": "refers_to",
                        "object": "light.living_ambient",
                        "entity_id": "light.living_ambient",
                        "source": "user_explicit",
                        "half_life_days": 365,
                        "natural_text": "客厅氛围灯指客厅氛围灯",
                    },
                ],
            },
            now,
        )

        parent = service.get("split_parent")
        child_a = service.get("split_child_a")
        child_b = service.get("split_child_b")
        edges = service.list_edges()
        self.assertEqual(parent.status, "superseded")
        self.assertEqual(sorted(parent.supersedes), ["split_child_a", "split_child_b"])
        self.assertEqual(child_a.derived_from_memory_ids, ["split_parent"])
        self.assertEqual(child_b.derived_from_memory_ids, ["split_parent"])
        self.assertIn("split_child_b", child_a.related_memory_ids)
        self.assertIn("split_child_a", child_b.related_memory_ids)
        relations = {(edge.source_id, edge.relation, edge.target_id) for edge in edges}
        self.assertIn(("split_child_a", "specializes", "split_parent"), relations)
        self.assertIn(("split_parent", "generalizes", "split_child_a"), relations)

    def test_merge_preserves_coverage_and_evidence_union(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_merge.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "merge_a",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "小书灯指书房台灯",
                "evidence_refs": [{"turn": "A"}],
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "merge_b",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "另一会话也说小书灯",
                "evidence_refs": [{"turn": "B"}],
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "merge",
                "source_ids": ["merge_a", "merge_b"],
                "coverage_proof": {"status": "provided", "sources": ["merge_a", "merge_b"]},
                "merged_record": {
                    "memory_id": "merge_ok",
                    "memory_type": "alias",
                    "scope": "entity",
                    "subject": "小书灯",
                    "predicate": "refers_to",
                    "object": "light.study_desk",
                    "entity_id": "light.study_desk",
                    "source": "user_explicit",
                    "half_life_days": 365,
                    "natural_text": "小书灯指书房台灯",
                    "evidence_refs": [{"turn": "merged"}],
                },
            },
            now,
        )

        merged = service.get("merge_ok")
        self.assertEqual(sorted(merged.merged_from), ["merge_a", "merge_b"])
        self.assertEqual(merged.coverage_proof, {"status": "provided", "sources": ["merge_a", "merge_b"]})
        self.assertEqual(len(merged.evidence_refs), 3)

    def test_merge_without_full_coverage_proof_rolls_back_sources(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_merge_invalid.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "merge_invalid_a",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "小书灯指书房台灯",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "merge_invalid_b",
                "memory_type": "alias",
                "scope": "entity",
                "subject": "小书灯",
                "predicate": "refers_to",
                "object": "light.study_desk",
                "entity_id": "light.study_desk",
                "source": "user_explicit",
                "half_life_days": 365,
                "natural_text": "另一会话也说小书灯",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "merge",
                "source_ids": ["merge_invalid_a", "merge_invalid_b"],
                "coverage_proof": {"status": "provided", "sources": ["merge_invalid_a"]},
                "merged_record": {
                    "memory_id": "merge_invalid_ok",
                    "memory_type": "alias",
                    "scope": "entity",
                    "subject": "小书灯",
                    "predicate": "refers_to",
                    "object": "light.study_desk",
                    "entity_id": "light.study_desk",
                    "source": "user_explicit",
                    "half_life_days": 365,
                    "natural_text": "小书灯指书房台灯",
                },
            },
            now,
        )

        merged = service.get("merge_invalid_ok")
        self.assertIsNone(merged.coverage_proof)
        result = service.maintenance(now + timedelta(days=1))
        self.assertIn("merge_invalid_ok", result["rollback_merge_ids"])
        self.assertEqual(service.get("merge_invalid_ok").status, "deleted")
        self.assertEqual(service.get("merge_invalid_a").status, "active")
        self.assertEqual(service.get("merge_invalid_b").status, "active")

    def test_reflection_candidate_promotes_on_maintenance(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_reflection.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_candidate",
                "memory_id": "reflection_candidate",
                "memory_type": "reflection",
                "scope": "entity",
                "subject": "门锁失败反思",
                "predicate": "future_rule",
                "object": "下次先提醒锁芯异常",
                "entity_id": "lock.front_door",
                "source": "execution_verification",
                "half_life_days": 90,
                "natural_text": "门锁 jam 后下次不要盲目重试",
            },
            now,
        )
        service.maintenance(now)
        record = service.get("reflection_candidate")
        self.assertEqual(record.status, "active")
        self.assertEqual(record.layer, "active")
        self.assertGreaterEqual(record.confidence, 0.70)

    def test_search_populates_global_constraints(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_constraints.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "constraint_preference",
                "memory_type": "preference",
                "scope": "entity",
                "subject": "睡前空调温度",
                "predicate": "preferred_temperature",
                "object": "26",
                "entity_id": "climate.bedroom_ac",
                "source": "user_explicit",
                "half_life_days": 180,
                "natural_text": "睡前空调温度偏好 26",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "constraint_routine",
                "memory_type": "routine",
                "scope": "routine",
                "subject": "睡前模式",
                "predicate": "routine_name",
                "object": "routine.sleep_mode",
                "source": "user_explicit",
                "half_life_days": 180,
                "natural_text": "睡前模式",
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_active",
                "memory_id": "constraint_reflection",
                "memory_type": "reflection",
                "scope": "entity",
                "subject": "门锁失败反思",
                "predicate": "future_rule",
                "object": "下次先检查锁芯",
                "entity_id": "lock.front_door",
                "source": "execution_verification",
                "half_life_days": 90,
                "natural_text": "门锁失败反思",
            },
            now,
        )
        package = service.search("睡前 门锁", task_type="control", now=now)
        self.assertTrue(package.global_constraints)
        self.assertEqual(
            {item.memory_type for item in package.global_constraints},
            {"preference", "routine", "reflection"},
        )


class AgentPlannerTest(unittest.TestCase):
    class _StubClient:
        def __init__(self, response=None, exc: Exception | None = None):
            self.response = response or {}
            self.exc = exc
            self.prompts: list[str] = []

        def invoke(self, prompt: str):
            self.prompts.append(prompt)
            if self.exc is not None:
                raise self.exc
            return self.response

    @staticmethod
    def _make_package(
        *,
        task_type: str = "control",
        candidates: list[dict] | None = None,
        matched_memories: list[dict] | None = None,
    ) -> SearchResultPackage:
        return SearchResultPackage(
            query="测试任务",
            task_type=task_type,
            candidate_devices=[
                CandidateDevice.model_validate(item)
                for item in (candidates or [])
            ],
            matched_memories=matched_memories or [],
        )

    def test_external_llm_plan_accepts_structured_single_action(self):
        stub = self._StubClient(
            response={
                "raw_output": json.dumps(
                    {
                        "actions": [
                            {
                                "service": "light.turn_on",
                                "entity_id": "light.study_desk",
                                "args": {},
                            }
                        ],
                        "should_ask_user": False,
                        "reason": "single_device_match",
                    },
                    ensure_ascii=False,
                ),
                "tool_calls": [{"name": "plan_only", "args": {}}],
                "usage": {"total_tokens": 42},
                "model": "stub-model",
                "provider": "stub-provider",
                "latency_ms": 12.5,
            }
        )
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            candidates=[
                {
                    "entity_id": "light.study_desk",
                    "name": "书房台灯",
                    "score": 0.92,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "capabilities": ["on_off"],
                    "available_services": ["light.turn_on", "light.turn_off"],
                    "current_state": {"state": "off", "attributes": {}},
                }
            ]
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "打开书房台灯")
        self.assertEqual(decision.backend, "external_llm")
        self.assertEqual(decision.action["service"], "light.turn_on")
        self.assertEqual(decision.model, "stub-model")
        self.assertEqual(decision.provider, "stub-provider")
        self.assertEqual(decision.tool_calls[0]["name"], "plan_only")
        self.assertEqual(decision.usage["total_tokens"], 42)
        self.assertIn("candidate_devices", stub.prompts[0])

    def test_external_llm_plan_preserves_multi_actions_for_non_safety_tasks(self):
        stub = self._StubClient(
            response={
                "raw_output": json.dumps(
                    {
                        "actions": [
                            {
                                "service": "light.turn_off",
                                "entity_id": "light.living_ceiling",
                                "args": {},
                            },
                            {
                                "service": "light.turn_on",
                                "entity_id": "light.living_ambient",
                                "args": {},
                            },
                        ],
                        "should_ask_user": False,
                        "reason": "movie_mode_steps",
                    },
                    ensure_ascii=False,
                ),
                "model": "stub-model",
                "provider": "stub-provider",
            }
        )
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            candidates=[
                {
                    "entity_id": "light.living_ceiling",
                    "name": "客厅顶灯",
                    "score": 0.91,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "available_services": ["light.turn_on", "light.turn_off"],
                },
                {
                    "entity_id": "light.living_ambient",
                    "name": "客厅氛围灯",
                    "score": 0.88,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "available_services": ["light.turn_on", "light.turn_off"],
                },
            ]
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "定义观影模式")
        self.assertEqual(decision.backend, "external_llm")
        self.assertEqual(len(decision.actions), 2)
        self.assertFalse(decision.should_ask_user)

    def test_external_llm_plan_honors_model_clarification(self):
        stub = self._StubClient(
            response={
                "raw_output": json.dumps(
                    {
                        "actions": [],
                        "should_ask_user": True,
                        "reason": "room_has_multiple_lights",
                    },
                    ensure_ascii=False,
                ),
            }
        )
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            candidates=[
                {
                    "entity_id": "light.bedroom_ceiling",
                    "name": "卧室顶灯",
                    "score": 0.81,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "available_services": ["light.turn_on", "light.turn_off"],
                },
                {
                    "entity_id": "light.bedroom_bedside",
                    "name": "卧室床头灯",
                    "score": 0.79,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "available_services": ["light.turn_on", "light.turn_off"],
                },
            ]
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "开卧室灯")
        self.assertTrue(decision.should_ask_user)
        self.assertEqual(decision.backend, "external_llm")
        self.assertEqual(decision.reason, "room_has_multiple_lights")

    def test_external_llm_plan_safety_multi_action_is_gated(self):
        stub = self._StubClient(
            response={
                "raw_output": json.dumps(
                    {
                        "actions": [
                            {
                                "service": "light.turn_off",
                                "entity_id": "light.bedroom_ceiling",
                                "args": {},
                            },
                            {
                                "service": "lock.lock",
                                "entity_id": "lock.front_door",
                                "args": {},
                            },
                        ],
                        "should_ask_user": False,
                        "reason": "sleep_mode",
                    },
                    ensure_ascii=False,
                ),
            }
        )
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            task_type="safety",
            candidates=[
                {
                    "entity_id": "light.bedroom_ceiling",
                    "name": "卧室顶灯",
                    "score": 0.85,
                    "confidence": 0.95,
                    "entity_type": "light",
                    "available_services": ["light.turn_on", "light.turn_off"],
                },
                {
                    "entity_id": "lock.front_door",
                    "name": "大门门锁",
                    "score": 0.84,
                    "confidence": 0.95,
                    "entity_type": "lock",
                    "available_services": ["lock.lock", "lock.unlock"],
                },
            ],
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "睡前模式")
        self.assertEqual(decision.backend, "external_llm")
        self.assertTrue(decision.should_ask_user)
        self.assertEqual(decision.actions, [])
        self.assertEqual(decision.reason, "safety_multi_action_requires_confirmation")

    def test_external_llm_prompt_marks_high_memory_worth_single_safety_action_as_directly_executable(self):
        package = self._make_package(
            task_type="safety",
            candidates=[
                {
                    "entity_id": "lock.front_door",
                    "name": "大门门锁",
                    "score": 0.92,
                    "confidence": 0.95,
                    "entity_type": "lock",
                    "available_services": ["lock.lock", "lock.unlock"],
                    "matched_memories": [
                        {
                            "memory_id": "b6_sleep_lock_pref",
                            "memory_type": "preference",
                            "text": "用户睡觉前希望锁上大门门锁",
                            "score": 0.92,
                            "raw_confidence": 0.90,
                            "effective_confidence": 0.90,
                            "memory_worth": 0.92,
                            "system_status": "active",
                            "true_status": "active",
                            "runtime_status": "active",
                            "layer": "active",
                            "in_usable_set": True,
                            "in_grounding_set": True,
                        }
                    ],
                }
            ],
            matched_memories=[
                {
                    "memory_id": "b6_sleep_lock_pref",
                    "memory_type": "preference",
                    "text": "用户睡觉前希望锁上大门门锁",
                    "score": 0.92,
                    "raw_confidence": 0.90,
                    "effective_confidence": 0.90,
                    "memory_worth": 0.92,
                    "system_status": "active",
                    "true_status": "active",
                    "runtime_status": "active",
                    "layer": "active",
                    "in_usable_set": True,
                    "in_grounding_set": True,
                }
            ],
        )
        prompt = _build_plan_prompt("睡觉了", package)
        self.assertIn('"direct_execution_allowed": true', prompt)
        self.assertIn('"entity_id": "lock.front_door"', prompt)
        self.assertIn("不要额外澄清", prompt)

    def test_external_llm_parse_failure_stays_external_with_failure_type(self):
        stub = self._StubClient(response={"raw_output": "先开灯，然后我来执行。"})
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            candidates=[
                {
                    "entity_id": "light.study_desk",
                    "name": "书房台灯",
                    "score": 0.92,
                    "confidence": 0.95,
                }
            ]
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "打开书房台灯")
        self.assertEqual(decision.backend, "external_llm")
        self.assertTrue(decision.failure_type.startswith("external_parse_failed"))
        self.assertIsNone(decision.action)
        self.assertFalse(decision.actions)

    def test_external_llm_call_failure_stays_external_with_failure_type(self):
        stub = self._StubClient(exc=RuntimeError("network down"))
        planner = AgentPlanner(client_factory=lambda: stub)
        package = self._make_package(
            candidates=[
                {
                    "entity_id": "light.study_desk",
                    "name": "书房台灯",
                    "score": 0.92,
                    "confidence": 0.95,
                }
            ]
        )
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            decision = planner.decide(package, "打开书房台灯")
        self.assertEqual(decision.backend, "external_llm")
        self.assertEqual(decision.failure_type, "external_call_failed:RuntimeError")
        self.assertIsNone(decision.action)
        self.assertFalse(decision.actions)

    def test_external_llm_client_uses_http_transport_when_langchain_missing(self):
        class _FakeResponse:
            def __init__(self, payload: dict):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        with mock.patch.object(
            ExternalLLMClient,
            "_load_runtime_config",
            return_value=("openai", "stub-model", "https://example.com/v1", "sk-test"),
        ), mock.patch.dict(sys.modules, {"langchain.chat_models": None}):
            with mock.patch("urllib.request.urlopen", return_value=_FakeResponse({"choices": [{"message": {"content": '{"actions":[],"should_ask_user":true,"reason":"clarify"}'}}], "usage": {"total_tokens": 12}, "model": "stub-model"})):
                client = ExternalLLMClient()
                response = client.invoke("测试 prompt")
        self.assertEqual(client._transport, "http")
        self.assertEqual(response["model"], "stub-model")
        self.assertEqual(response["provider"], "openai")
        self.assertEqual(response["usage"]["total_tokens"], 12)
        self.assertIn('"should_ask_user"', response["raw_output"])

    def test_external_llm_client_http_transport_passes_requested_seed(self):
        captured = {}

        class _FakeResponse:
            def __init__(self, payload: dict):
                self._payload = payload

            def read(self):
                return json.dumps(self._payload, ensure_ascii=False).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def _fake_urlopen(request, timeout=20):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(
                {
                    "choices": [{"message": {"content": '{"actions":[],"should_ask_user":true,"reason":"clarify"}'}}],
                    "usage": {"total_tokens": 9},
                    "model": "stub-model",
                    "system_fingerprint": "fp-stub",
                }
            )

        with mock.patch.object(
            ExternalLLMClient,
            "_load_runtime_config",
            return_value=("openai", "stub-model", "https://example.com/v1", "sk-test"),
        ), mock.patch.dict(sys.modules, {"langchain.chat_models": None}):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                client = ExternalLLMClient()
                response = client.invoke("测试 prompt", requested_seed=1002)
        self.assertEqual(captured["body"]["seed"], 1002)
        self.assertEqual(response["requested_seed"], 1002)
        self.assertTrue(response["request_seed_supported"])
        self.assertTrue(response["request_seed_applied"])
        self.assertEqual(response["seed_protocol"], "provider_seed")

    def test_external_llm_client_replicates_seed_when_proxy_rejects_seed_request(self):
        calls = []

        class _FakeResponse:
            def read(self):
                return json.dumps(
                    {
                        "choices": [{"message": {"content": '{"actions":[]}'}}],
                        "usage": {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6},
                        "model": "stub-model",
                    }
                ).encode("utf-8")

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        def _fake_urlopen(request, timeout=20):
            body = json.loads(request.data.decode("utf-8"))
            calls.append(body)
            if "seed" in body:
                raise urllib.error.HTTPError(
                    request.full_url,
                    503,
                    "proxy unavailable",
                    hdrs=None,
                    fp=io.BytesIO(b'{"error":"proxy_unavailable"}'),
                )
            return _FakeResponse()

        with mock.patch.object(
            ExternalLLMClient,
            "_load_runtime_config",
            return_value=("openai", "stub-model", "https://example.com/v1", "sk-test"),
        ), mock.patch.dict(sys.modules, {"langchain.chat_models": None}):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                client = ExternalLLMClient()
                response = client.invoke("测试 prompt", requested_seed=1002)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["seed"], 1002)
        self.assertNotIn("seed", calls[1])
        self.assertEqual(response["requested_seed"], 1002)
        self.assertFalse(response["request_seed_supported"])
        self.assertFalse(response["request_seed_applied"])
        self.assertEqual(response["seed_protocol"], "replicate_id")
        self.assertEqual(response["response_metadata"]["seed_fallback"], "http_503")

    def test_run_agent_scenario_records_structured_decision_and_execution_results(self):
        scenario = load_scenario(Path("experiments/scenarios/category_e/E1.yaml"))
        fake_decision = AgentPlannerDecision(
            action={"service": "routine.run", "entity_id": "routine.movie_mode", "args": {}},
            actions=[{"service": "routine.run", "entity_id": "routine.movie_mode", "args": {}}],
            should_ask_user=False,
            reason="stubbed_routine",
            raw_output='{"actions":[{"service":"routine.run","entity_id":"routine.movie_mode","args":{}}],"should_ask_user":false,"reason":"stubbed_routine"}',
            structured_output={
                "actions": [{"service": "routine.run", "entity_id": "routine.movie_mode", "args": {}}],
                "should_ask_user": False,
                "reason": "stubbed_routine",
                "raw_model_output": "stub",
                "model": "stub-model",
                "provider": "stub-provider",
                "tool_calls": [],
                "backend": "external_llm",
            },
            backend="external_llm",
            provider="stub-provider",
            model="stub-model",
            usage={"total_tokens": 33},
            latency_ms=8.0,
            requested_seed=1001,
            request_seed_supported=True,
            request_seed_applied=True,
            seed_protocol="provider_seed",
        )
        with mock.patch("experiments.runner.single_run.AgentAdapter") as adapter_cls:
            adapter_cls.return_value.plan.return_value = fake_decision
            trace = run_agent_scenario(scenario, seed=1001, results_root=Path("experiments/results"))
        self.assertEqual(trace["agent_backend"], "external_llm")
        self.assertEqual(trace["agent_model"], "stub-model")
        self.assertEqual(trace["agent_provider"], "stub-provider")
        self.assertEqual(trace["agent_requested_seed"], 1001)
        self.assertTrue(trace["agent_request_seed_supported"])
        self.assertTrue(trace["agent_request_seed_applied"])
        self.assertEqual(trace["agent_seed_protocol"], "provider_seed")
        self.assertTrue(trace["agent_structured_decisions"])
        self.assertEqual(trace["chosen_actions"][0]["service"], "routine.run")
        self.assertTrue(trace["action_execution_results"])
        self.assertTrue(trace["task_success"])

    def test_run_agent_scenario_without_say_steps_keeps_external_backend_marker(self):
        scenario = load_scenario(Path("experiments/scenarios/category_f/F1.yaml"))
        with mock.patch.dict(os.environ, {"EXPERIMENT_AGENT_BACKEND": "external"}, clear=False):
            trace = run_agent_scenario(scenario, seed=1001, results_root=Path("experiments/results"))
        self.assertEqual(trace["agent_backend"], "external_llm")
        self.assertEqual(trace["agent_seed_protocol"], "no_agent_call_required")
        self.assertEqual(trace["agent_api_call_count"] if "agent_api_call_count" in trace else 0, 0)
        self.assertFalse(trace["agent_usage_metadata"])
        self.assertTrue(trace["task_success"])

    def test_run_agent_scenario_invalid_action_args_becomes_behavior_failure(self):
        scenario = load_scenario(Path("experiments/scenarios/category_h/H2.yaml"))
        fake_decision = AgentPlannerDecision(
            action={"service": "climate.set_temperature", "entity_id": "climate.bedroom_ac", "args": {}},
            actions=[{"service": "climate.set_temperature", "entity_id": "climate.bedroom_ac", "args": {}}],
            should_ask_user=False,
            reason="stubbed_invalid_action",
            backend="external_llm",
            requested_seed=1001,
            request_seed_supported=True,
            request_seed_applied=True,
            seed_protocol="provider_seed",
        )
        with mock.patch("experiments.runner.single_run.AgentAdapter") as adapter_cls:
            adapter_cls.return_value.plan.return_value = fake_decision
            trace = run_agent_scenario(scenario, seed=1001, results_root=Path("experiments/results"))
        self.assertFalse(trace["task_success"])
        self.assertEqual(trace["outcome"], "failure")
        self.assertTrue(trace["action_execution_results"])
        self.assertFalse(trace["action_execution_results"][-1]["success"])
        self.assertEqual(trace["action_execution_results"][-1]["error"], "missing_required_args")

    def test_run_agent_scenario_parse_failure_is_not_rewritten_as_heuristic_success(self):
        scenario = load_scenario(Path("experiments/scenarios/category_g/G3.yaml"))
        fake_decision = AgentPlannerDecision(
            action=None,
            actions=[],
            should_ask_user=False,
            reason="external_parse_failed:ValueError",
            backend="external_llm",
            raw_output='{"actions":[{"service":"light.turn_on","entity_id":"light.living_ceiling","args":{"color_temp":暖色}}]}',
            structured_output={"backend": "external_llm", "failure_type": "external_parse_failed:ValueError"},
            failure_type="external_parse_failed:ValueError",
            requested_seed=1001,
            request_seed_supported=True,
            request_seed_applied=True,
            seed_protocol="provider_seed",
        )
        with mock.patch("experiments.runner.single_run.AgentAdapter") as adapter_cls:
            adapter_cls.return_value.plan.return_value = fake_decision
            trace = run_agent_scenario(scenario, seed=1001, results_root=Path("experiments/results"))
        self.assertEqual(trace["agent_backend"], "external_llm")
        self.assertIn("external_parse_failed:ValueError", trace["agent_failures"])
        self.assertFalse(trace["task_success"])
        self.assertEqual(trace["outcome"], "failure")


class RunnerSmokeTest(unittest.TestCase):
    def test_batch_run_generates_metrics(self):
        scenario = Path("experiments/scenarios/category_a/A1.yaml")
        loaded = load_scenario(scenario)
        self.assertEqual(loaded["scenario_id"], "A1")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"))
        self.assertIn("TSR", result["metrics"])
        manifest_path = Path("experiments/results/reports/dev/Ours/oracle/manifest.json")
        self.assertTrue(manifest_path.exists())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertIn("generated_at", manifest)
        self.assertIn("resolved_config", manifest)
        self.assertIn("failed_task_ids", manifest)
        self.assertIsInstance(manifest["failed_task_ids"], list)

    def test_task_success_and_assertion_results_are_structured(self):
        scenario = Path("experiments/scenarios/category_a/A1.yaml")
        result = run_batch(
            [scenario],
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_task_success_structure",
        )
        trace = result["traces"][0]
        self.assertTrue(trace["task_success"])
        self.assertTrue(trace["action_success"])
        self.assertTrue(trace["memory_assertion_success"])
        self.assertTrue(trace["final_state_success"])
        self.assertTrue(trace["assertion_results"])
        self.assertTrue(all("kind" in item and "success" in item for item in trace["assertion_results"]))

    def test_registry_fallback_for_archived_alias(self):
        scenario = Path("experiments/scenarios/category_a/A6.yaml")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), run_id="test_fallback")
        self.assertIn("TSR", result["metrics"])
        self.assertGreaterEqual(result["metrics"]["TSR"], 1.0)

    def test_candidate_promotion_allows_control(self):
        scenario = Path("experiments/scenarios/category_b/B2.yaml")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), run_id="test_b2")
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_thin_scenarios_have_specific_assertions(self):
        scenarios = [
            Path("experiments/scenarios/category_a/A1.yaml"),
            Path("experiments/scenarios/category_b/B3.yaml"),
            Path("experiments/scenarios/category_b/B4.yaml"),
            Path("experiments/scenarios/category_e/E2.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_query_and_automation_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_h/H1.yaml"),
            Path("experiments/scenarios/category_h/H2.yaml"),
            Path("experiments/scenarios/category_c/C1.yaml"),
            Path("experiments/scenarios/category_c/C4.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_query_automation_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_mutation_and_absence_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_d/D1.yaml"),
            Path("experiments/scenarios/category_d/D2.yaml"),
            Path("experiments/scenarios/category_g/G1.yaml"),
            Path("experiments/scenarios/category_g/G5.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_mutation_absence_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_revision_validity_and_safety_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_a/A2.yaml"),
            Path("experiments/scenarios/category_a/A5.yaml"),
            Path("experiments/scenarios/category_b/B1.yaml"),
            Path("experiments/scenarios/category_b/B6.yaml"),
            Path("experiments/scenarios/category_c/C2.yaml"),
            Path("experiments/scenarios/category_c/C3.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_revision_validity_safety_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_habit_and_routine_threshold_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_b/B5.yaml"),
            Path("experiments/scenarios/category_c/C1.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_habit_routine_threshold_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_expiry_and_threshold_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_b/B1.yaml"),
            Path("experiments/scenarios/category_b/B4.yaml"),
            Path("experiments/scenarios/category_c/C2.yaml"),
            Path("experiments/scenarios/category_c/C3.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_expiry_threshold_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_noise_and_threshold_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_g/G2.yaml"),
            Path("experiments/scenarios/category_g/G3.yaml"),
            Path("experiments/scenarios/category_g/G4.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_noise_threshold_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_safety_reflection_and_delete_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_e/E3.yaml"),
            Path("experiments/scenarios/category_f/F7.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_safety_reflection_delete_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_candidate_isolation_resampling_and_split_thin_specs(self):
        scenarios = [
            Path("experiments/scenarios/category_a/A3.yaml"),
            Path("experiments/scenarios/category_a/A4.yaml"),
            Path("experiments/scenarios/category_f/F5.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_candidate_isolation_resampling_split_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_habit_candidate_promotion_requires_recent_support_and_no_counterexample(self):
        db_path = Path(tempfile.gettempdir()) / "memory_service_habit_promotion.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = HAOracle().current_time
        service.apply_memory_op(
            {
                "op": "add_candidate",
                "memory_id": "habit_old_window",
                "memory_type": "habit",
                "scope": "entity",
                "subject": "睡前空调温度",
                "predicate": "prefers_temperature",
                "object": "24",
                "entity_id": "climate.bedroom_ac",
                "source": "user_behavior",
                "half_life_days": 90,
                "natural_text": "用户睡前把空调设为24度",
                "positive_hits": 3,
                "structured_payload": {
                    "observation_timestamps": [
                        "2026-01-01T20:00:00+08:00",
                        "2026-01-05T20:00:00+08:00",
                        "2026-01-09T20:00:00+08:00",
                    ]
                },
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_candidate",
                "memory_id": "habit_counterexample",
                "memory_type": "habit",
                "scope": "entity",
                "subject": "睡前空调温度",
                "predicate": "prefers_temperature",
                "object": "25",
                "entity_id": "climate.bedroom_ac",
                "source": "user_behavior",
                "half_life_days": 90,
                "natural_text": "用户睡前把空调设为25度",
                "positive_hits": 3,
                "negative_hits": 1,
                "structured_payload": {
                    "observation_timestamps": [
                        "2026-01-01T20:00:00+08:00",
                        "2026-01-03T20:00:00+08:00",
                        "2026-01-05T20:00:00+08:00",
                    ]
                },
            },
            now,
        )
        service.apply_memory_op(
            {
                "op": "add_candidate",
                "memory_id": "habit_promotable",
                "memory_type": "habit",
                "scope": "entity",
                "subject": "睡前空调温度",
                "predicate": "prefers_temperature",
                "object": "26",
                "entity_id": "climate.bedroom_ac",
                "source": "user_behavior",
                "half_life_days": 90,
                "natural_text": "用户睡前把空调设为26度",
                "positive_hits": 3,
                "structured_payload": {
                    "observation_timestamps": [
                        "2026-01-01T20:00:00+08:00",
                        "2026-01-03T20:00:00+08:00",
                        "2026-01-05T20:00:00+08:00",
                    ]
                },
            },
            now,
        )
        service.maintenance(now + timedelta(days=8))
        old_window = service.get("habit_old_window")
        counterexample = service.get("habit_counterexample")
        promotable = service.get("habit_promotable")
        self.assertEqual(old_window.status, "candidate")
        self.assertEqual(counterexample.status, "candidate")
        self.assertEqual(promotable.status, "active")

    def test_revise_and_merge_paths(self):
        scenarios = [
            Path("experiments/scenarios/category_a/A2.yaml"),
            Path("experiments/scenarios/category_f/F6.yaml"),
        ]
        result = run_batch(scenarios, seed=1001, results_root=Path("experiments/results"), run_id="test_a2_f6")
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_capability_change_and_registry_override(self):
        scenarios = [
            Path("experiments/scenarios/category_d/D2.yaml"),
            Path("experiments/scenarios/category_g/G3.yaml"),
        ]
        result = run_batch(scenarios, seed=1001, results_root=Path("experiments/results"), run_id="test_d2_g3")
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_capability_routine_fallback_thin_specs(self):
        scenario = Path("experiments/scenarios/category_d/D2.yaml")
        result = run_batch(
            [scenario],
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_capability_routine_fallback_thin_specs",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_agent_safety_and_high_value_preference(self):
        scenarios = [
            Path("experiments/scenarios/category_b/B6.yaml"),
            Path("experiments/scenarios/category_e/E2.yaml"),
            Path("experiments/scenarios/category_e/E3.yaml"),
            Path("experiments/scenarios/category_g/G1.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_agent_safety",
            planner_mode="agent",
        )
        self.assertGreaterEqual(result["metrics"]["TSR"], 0.75)
        self.assertGreaterEqual(result["metrics"]["UAA"], 0.5)

    def test_agent_movie_mode_routine(self):
        scenario = Path("experiments/scenarios/category_e/E1.yaml")
        result = run_batch(
            [scenario],
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_agent_movie",
            planner_mode="agent",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_real_llm_seal_readiness_audit_script(self):
        proc = subprocess.run(
            [sys.executable, "experiments/scripts/audit_real_llm_seal_readiness.py"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            check=True,
        )
        out_path = Path(proc.stdout.strip())
        self.assertTrue(out_path.exists())
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["status"], "candidate_only")
        self.assertTrue(any(item["code"] == "secondary_seed_target_reached" for item in payload["failures"]))

    def test_query_vs_control_threshold_tiers(self):
        scenarios = [
            Path("experiments/scenarios/category_h/H1.yaml"),
            Path("experiments/scenarios/category_h/H2.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_h_tiers",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_correction_window_and_distractor_paths(self):
        scenarios = [
            Path("experiments/scenarios/category_a/A5.yaml"),
            Path("experiments/scenarios/category_c/C2.yaml"),
            Path("experiments/scenarios/category_g/G2.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_acg_paths",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_relation_conflict_and_ripple_paths(self):
        scenarios = [
            Path("experiments/scenarios/category_d/D3.yaml"),
            Path("experiments/scenarios/category_f/F1.yaml"),
            Path("experiments/scenarios/category_f/F2.yaml"),
            Path("experiments/scenarios/category_f/F3.yaml"),
            Path("experiments/scenarios/category_f/F4.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_d3f_paths",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_absent_memory_negative_controls(self):
        scenarios = [
            Path("experiments/scenarios/category_g/G1.yaml"),
            Path("experiments/scenarios/category_g/G5.yaml"),
        ]
        result = run_batch(
            scenarios,
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_negative_absent",
            planner_mode="agent",
        )
        self.assertEqual(result["metrics"]["TSR"], 1.0)

    def test_multi_seed_summary_outputs(self):
        scenario = Path("experiments/scenarios/category_a/A1.yaml")
        result = run_batch_multi_seed(
            [scenario],
            seeds=[1001, 1002],
            results_root=Path("experiments/results"),
            run_id="test_multi_seed",
        )
        self.assertIn("summary", result)
        self.assertIn("TSR", result["summary"])
        self.assertTrue(Path("experiments/results/aggregated_metrics/test_multi_seed/Ours/oracle/per_scenario.multi_seed.csv").exists())

    def test_sync_ground_truth_creates_annotation_placeholders(self):
        root = Path("experiments")
        subprocess.run(
            [sys.executable, "experiments/scripts/sync_ground_truth.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        gt_files = sorted((root / "annotations" / "scenario_ground_truth").glob("*.json"))
        ia_files = sorted((root / "annotations" / "inter_annotator").glob("*.json"))
        self.assertEqual(len(gt_files), 36)
        self.assertEqual(len(ia_files), 36)
        a1_payload = json.loads((root / "annotations" / "scenario_ground_truth" / "A1.json").read_text(encoding="utf-8"))
        self.assertEqual(a1_payload["title"], "A1")
        self.assertEqual(a1_payload["category"], "A")
        self.assertEqual(a1_payload["rq_tags"], ["RQ3"])

    def test_configured_experiments_script_multi_seed_outputs(self):
        env = dict(os.environ)
        env["MAX_PRIMARY_SEEDS"] = "1"
        env["MAX_SECONDARY_SEEDS"] = "1"
        subprocess.run(
            [sys.executable, "experiments/scripts/run_configured_experiments.py"],
            cwd=Path.cwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        oracle_summary = Path("experiments/results/aggregated_metrics/configured_oracle_dev/Ours/oracle/metrics.summary.json")
        agent_summary = Path("experiments/results/aggregated_metrics/configured_agent_dev/Ours/agent/metrics.summary.json")
        self.assertTrue(oracle_summary.exists())
        self.assertTrue(agent_summary.exists())

    def test_generate_report_creates_markdown_summary(self):
        subprocess.run(
            [sys.executable, "experiments/scripts/generate_report.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        report = Path("experiments/results/reports/dev/generated_experiment_summary.md")
        self.assertTrue(report.exists())
        text = report.read_text(encoding="utf-8")
        self.assertIn("实验结果摘要", text)
        self.assertIn("Ours 结果概览", text)
        self.assertIn("TSR_cohen_d", text)

    def test_generate_report_does_not_replace_paper_summary_without_opt_in(self):
        summary = Path("docs/实验结果摘要.md")
        before = summary.read_text(encoding="utf-8")
        subprocess.run(
            [sys.executable, "experiments/scripts/generate_report.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(summary.read_text(encoding="utf-8"), before)

    def test_generate_statistics_outputs(self):
        subprocess.run(
            [sys.executable, "experiments/scripts/generate_statistics.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        stats_json = Path("experiments/results/reports/dev/statistics_summary.json")
        stats_csv = Path("experiments/results/reports/dev/statistics_summary.csv")
        self.assertTrue(stats_json.exists())
        self.assertTrue(stats_csv.exists())
        payload = json.loads(stats_json.read_text(encoding="utf-8"))
        self.assertTrue(payload)
        self.assertIn("TSR_cohen_d_vs_ours", payload[0])
        self.assertIn("TSR_holm_adjusted_p_vs_ours", payload[0])

    def test_generate_run_index_outputs(self):
        subprocess.run(
            [sys.executable, "experiments/scripts/generate_run_index.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        index_path = Path("experiments/results/reports/dev/run_index.json")
        self.assertTrue(index_path.exists())
        data = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertTrue(isinstance(data, list))
        self.assertTrue(data)
        self.assertTrue(data[0].get("generated_at"))
        self.assertIn("failed_task_count", data[0])
        self.assertIn("failed_task_ids", data[0])

    def test_generate_significance_outputs(self):
        subprocess.run(
            [sys.executable, "experiments/scripts/generate_significance.py"],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        out_path = Path("experiments/results/reports/dev/significance_summary.json")
        self.assertTrue(out_path.exists())
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertTrue(payload)
        first_metrics = payload[0]["metrics"]
        self.assertIn("cohen_d", first_metrics["TSR"])
        self.assertIn("holm_adjusted_p", first_metrics["TSR"])

    def test_failed_trace_does_not_count_as_tsr_success(self):
        trace = {
            "outcome": "failure",
            "final_device_state": {"light.study_desk": {"state": "on", "attributes": {}}},
            "ground_truth_state": {"light.study_desk": {"state": "on", "attributes": {}}},
        }
        self.assertEqual(task_metrics(trace)["TSR"], 0.0)

    def test_non_applicable_final_state_is_not_aggregated_as_zero(self):
        trace = {
            "scenario_id": "G1",
            "sim_time": HAOracle().current_time.isoformat(),
            "task_success": True,
            "outcome": "success",
            "final_state_success": None,
        }
        metrics = aggregate_task_metrics([trace])
        self.assertEqual(metrics["TSR"], 1.0)
        self.assertIsNone(metrics["State TSR"])
        self.assertIsNone(metrics["final_state_success"])

    def test_physical_action_scenarios_have_final_state_contracts(self):
        missing = []
        for path in sorted(Path("experiments/scenarios").rglob("*.yaml")):
            scenario = load_scenario(path)
            physical_actions = [
                step
                for step in scenario["steps"]
                if step["type"] == "expect_action"
                and step.get("assert", {}).get("service") != "memory.answer"
            ]
            if physical_actions and not any(step["type"] == "expect_final_state" for step in scenario["steps"]):
                missing.append(scenario["scenario_id"])
        self.assertEqual(missing, [])

    def test_b4_large_context_is_not_equivalent_to_b0(self):
        scenario = Path("experiments/scenarios/category_a/A1.yaml")
        b0 = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), system_id="B0", run_id="test_b4")
        b4 = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), system_id="B4", run_id="test_b4")
        self.assertNotEqual(b0["metrics"]["TSR"], b4["metrics"]["TSR"])
        self.assertGreater(b4["metrics"]["Estimated Prompt Tokens"], b0["metrics"]["Estimated Prompt Tokens"])

    def test_agent_fallback_has_no_scenario_text_special_cases(self):
        source = Path("experiments/planners/agent_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("观影模式", source)
        self.assertNotIn("睡前模式", source)

    def test_prompt_tokens_nonzero_in_main_run(self):
        result = run_batch(
            [Path("experiments/scenarios/category_a/A1.yaml")],
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_prompt_tokens",
        )
        self.assertGreater(result["metrics"]["Estimated Prompt Tokens"], 0.0)

    def test_v4_metrics_separate_external_success_and_real_usage(self):
        trace = {
            "scenario_id": "pilot",
            "external_task_success": True,
            "task_success": False,
            "assertion_results": [{"kind": "memory", "success": False}],
            "agent_usage_metadata": [{
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
            }],
        }
        metrics = task_metrics(trace)
        self.assertEqual(metrics["TSR"], 1.0)
        self.assertEqual(metrics["Contract Conformance Score"], 0.0)
        self.assertEqual(metrics["Prompt Tokens"], 100.0)
        self.assertEqual(metrics["Completion Tokens"], 20.0)

    def test_v4_metric_eligible_sets_are_evaluator_bound(self):
        now = HAOracle().current_time
        trace = {
            "scenario_id": "C2_v4_behavioral",
            "evaluation_protocol": "v4",
            "external_task_success": True,
            "chosen_action": {"service": "light.turn_on", "entity_id": "light.study_desk", "args": {}},
            "evaluator_preference_match_eligible": False,
            "evaluator_safety_gate_required": False,
            "safety_relevant": True,
            "safety_gated": False,
            "evaluator_correction_pairs": [{"old_id": "old", "new_id": "new"}],
            "steps": [{"retrieved_memories": [
                {"memory_id": "old", "in_usable_set": False, "in_grounding_set": False, "evaluator_true_status": "superseded"},
                {"memory_id": "new", "in_usable_set": True, "in_grounding_set": True, "effective_confidence": 0.8},
            ]}],
            "memory_records_after": [{
                "memory_id": "idle", "access_count": 0, "half_life_days": 2,
                "created_at": (now - timedelta(days=30)).isoformat(),
                "observed_at": None, "updated_at": (now - timedelta(days=1)).isoformat(),
            }],
            "sim_time": now.isoformat(),
        }
        metrics = task_metrics(trace)
        self.assertIsNone(metrics["PM"])
        self.assertIsNone(metrics["UAA"])
        self.assertEqual(metrics["UC"], 1.0)
        self.assertEqual(metrics["SRR"], 0.0)
        self.assertEqual(metrics["DMR"], 0.0)

    def test_v4_uaa_and_pm_require_explicit_evaluator_labels(self):
        trace = {
            "external_task_success": False,
            "chosen_action": {"service": "lock.lock", "entity_id": "lock.front_door", "args": {}},
            "evaluator_preference_match_eligible": True,
            "evaluator_preferred_action": {"service": "lock.lock", "entity_id": "lock.front_door", "args": {}},
            "evaluator_safety_gate_required": True,
            "safety_gated": True,
        }
        metrics = task_metrics(trace)
        self.assertEqual(metrics["PM"], 1.0)
        self.assertEqual(metrics["UAA"], 1.0)
        trace["evaluator_safety_gate_required"] = False
        self.assertIsNone(task_metrics(trace)["UAA"])
        self.assertIsNone(task_metrics(trace)["Unsafe Action Rate"])

    def test_v4_primary_metrics_have_positive_negative_and_boundary_cases(self):
        trace = {
            "task_type": "automation",
            "evaluator_safety_gate_required": True,
            "safety_gated": False,
            "assertion_results": [
                {"kind": "action", "success": True, "expected": {"service": "light.turn_on"}},
                {"kind": "final_state", "success": False},
                {"kind": "clarification", "success": True},
            ],
        }
        metrics = task_metrics(trace)
        self.assertEqual(metrics["Automation Decision Accuracy"], 1.0)
        self.assertEqual(metrics["Control Final-State TSR"], 0.0)
        self.assertEqual(metrics["Necessary Clarification Rate"], 1.0)
        self.assertEqual(metrics["Unsafe Action Rate"], 1.0)
        self.assertIsNone(metrics["Query Answer Accuracy"])
        trace["assertion_results"] = []
        trace["evaluator_safety_gate_required"] = None
        self.assertIsNone(task_metrics(trace)["Automation Decision Accuracy"])
        self.assertIsNone(task_metrics(trace)["Unsafe Action Rate"])

    def test_v4_query_expect_action_is_scored_as_query_metric(self):
        scenario = load_scenario(Path("experiments/scenarios/protocol_v4/Q1.yaml"))
        trace = run_agent_scenario(scenario, seed=1001)
        self.assertTrue(trace["task_success"])
        self.assertEqual(task_metrics(trace)["Query Answer Accuracy"], 1.0)
        self.assertEqual(trace["assertion_results"][-1]["kind"], "query")

    def test_v4_second_world_runner_uses_scenario_world_path(self):
        scenario = load_scenario(Path("experiments/scenarios/protocol_v4/B6W.yaml"))
        trace = run_agent_scenario(scenario, seed=1001)
        self.assertEqual(trace["world_version"], "wm-v2-alt-home")

    def test_v4_formal_statistics_require_full_frozen_behavioral_coverage(self):
        from experiments.scripts.analyze_protocol_v4_formal import _formal_behavioral_coverage

        coverage = _formal_behavioral_coverage({})
        self.assertFalse(coverage["complete"])
        self.assertEqual(coverage["expected_units"], 2100)
        self.assertEqual(coverage["observed_units"], 0)

    def test_v4_two_way_cluster_bootstrap_and_metric_direction(self):
        rows = [
            {"scenario_id": "s1", "replicate_id": 1, "delta": 1.0},
            {"scenario_id": "s1", "replicate_id": 2, "delta": 1.0},
            {"scenario_id": "s2", "replicate_id": 1, "delta": -1.0},
            {"scenario_id": "s2", "replicate_id": 2, "delta": -1.0},
        ]
        point, low, high = two_way_cluster_bootstrap(rows, samples=400, rng_seed=7)
        self.assertEqual(point, 0.0)
        self.assertLess(low, 0.0)
        self.assertGreater(high, 0.0)
        self.assertEqual(METRIC_SPECS["TSR"]["direction"], "higher")
        self.assertEqual(METRIC_SPECS["WDR"]["direction"], "lower")
        self.assertGreater((-1.0) * -1.0, 0.0)

    def test_v4_mcnemar_and_global_holm_family(self):
        paired = [
            {"ours": 1.0, "baseline": 1.0},
            {"ours": 1.0, "baseline": 0.0},
            {"ours": 1.0, "baseline": 0.0},
            {"ours": 0.0, "baseline": 1.0},
            {"ours": 0.0, "baseline": 0.0},
        ]
        result = mcnemar_exact(paired)
        self.assertEqual(result["both_success"], 1)
        self.assertEqual(result["ours_only_success"], 2)
        self.assertEqual(result["baseline_only_success"], 1)
        self.assertEqual(result["both_failure"], 1)
        self.assertFalse(result["clustered_gee_used"])
        self.assertLess(paired_sign_flip_p_value([{"delta": 1.0}] * 8, samples=2000, rng_seed=3), 0.02)
        rows = [{"raw_p_value": 0.01}, {"raw_p_value": 0.04}, {"raw_p_value": 0.03}]
        holm_adjust(rows)
        self.assertEqual({row["holm_family_size"] for row in rows}, {3})
        self.assertAlmostEqual(rows[0]["holm_adjusted_p"], 0.03)

    def test_v4_trace_collection_separates_failure_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            trace_root = root / "raw_traces" / "run"
            trace_root.mkdir(parents=True)
            common = {
                "evaluation_protocol": "v4",
                "agent_backend": "external_llm",
                "agent_seed_protocol": "replicate_id",
                "agent_usage_metadata": [{"prompt_tokens": 10, "completion_tokens": 2}],
                "system_id": "Ours",
                "scenario_id": "Q1_v4_behavioral",
                "seed": 1001,
            }
            (trace_root / "valid.json").write_text(json.dumps(common), encoding="utf-8")
            transport = {**common, "seed": 1002, "agent_failures": ["external_call_failed:TimeoutError"]}
            (trace_root / "transport.json").write_text(json.dumps(transport), encoding="utf-8")
            no_call = {**common, "seed": 1003, "agent_seed_protocol": "no_agent_call_required"}
            (trace_root / "no_call.json").write_text(json.dumps(no_call), encoding="utf-8")
            no_usage = {**common, "seed": 1004, "agent_usage_metadata": []}
            (trace_root / "no_usage.json").write_text(json.dumps(no_usage), encoding="utf-8")
            model_failure = {**common, "seed": 1005, "agent_failures": ["external_parse_failed:ValueError"]}
            (trace_root / "model_failure.json").write_text(json.dumps(model_failure), encoding="utf-8")
            by_key, exclusions = collect_traces(root)
        self.assertEqual(len(by_key), 2)
        self.assertEqual(exclusions["counts"]["transport_failure"], 1)
        self.assertEqual(exclusions["counts"]["no_agent_call_required"], 1)
        self.assertEqual(exclusions["counts"]["real_usage_missing"], 1)
        self.assertEqual(exclusions["included_model_behavior_failure_counts"]["external_parse_failed:ValueError"], 1)

    def test_v4_guard_override_is_classified_from_external_truth(self):
        trace = {
            "system_id": "Ours",
            "scenario_id": "Q1_v4_behavioral",
            "seed": 1001,
            "raw_planner_decisions": [{"step_id": "say", "actions": [], "should_ask_user": False}],
            "guarded_planner_decisions": [{"step_id": "say", "actions": [{"service": "memory.answer", "entity_id": "q1", "args": {}}], "should_ask_user": False}],
            "assertion_results": [{"kind": "query", "step_id": "expect", "success": True, "expected": {"service": "memory.answer", "entity_id": "q1", "args": {}}}],
        }
        diagnostics = guard_diagnostics([trace])
        self.assertEqual(diagnostics["override_classification"]["corrected"], 1)
        self.assertEqual(diagnostics["override_classification"]["harmful"], 0)
        self.assertEqual(diagnostics["raw_planner_accuracy"], 0.0)
        self.assertEqual(diagnostics["guarded_planner_accuracy"], 1.0)

    def test_v4_preflight_audit_accepts_complete_hermetic_single_replicate(self):
        systems = ["Ours", "B0", "B1", "B2", "B3", "B4", "B5"]
        scenarios = ["H2_v4_behavioral", "C2_v4_behavioral", "B6_v4_behavioral", "Q1_v4_behavioral", "U1_v4_behavioral", "R1_v4_behavioral", "D1_v4_behavioral", "S1_v4_behavioral", "V1_v4_behavioral", "E4_v4_behavioral"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run_id = "hermetic_preflight"
            for system_id in systems:
                for scenario_id in scenarios:
                    trace_dir = root / "raw_traces" / run_id / system_id / "agent" / scenario_id
                    report_dir = root / "reports" / run_id / system_id / "agent" / scenario_id
                    trace_dir.mkdir(parents=True, exist_ok=True)
                    report_dir.mkdir(parents=True, exist_ok=True)
                    retrieval_metadata = {}
                    if system_id == "B1":
                        retrieval_metadata["baseline_context_source"] = "raw_text_rag"
                    elif system_id == "B4":
                        retrieval_metadata["baseline_context_source"] = "full_raw_history"
                    assertion = {"kind": "query", "success": True, "expected": {"service": "memory.answer", "entity_id": "q1", "args": {}}} if scenario_id == "Q1_v4_behavioral" else {"kind": "action", "success": True, "expected": {"service": "light.turn_on", "entity_id": "light.study_desk", "args": {}}}
                    trace = {
                        "evaluation_protocol": "v4", "agent_backend": "external_llm", "agent_seed_protocol": "replicate_id",
                        "agent_usage_metadata": [{"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}],
                        "agent_failures": [], "system_id": system_id, "scenario_id": scenario_id, "seed": 1001,
                        "external_task_success": True, "task_success": True, "assertion_results": [assertion],
                        "steps": [{"retrieval_metadata": retrieval_metadata}], "memory_records_after": [],
                        "raw_planner_decisions": [], "guarded_planner_decisions": [], "guard_overrides": [],
                    }
                    (trace_dir / "1001.json").write_text(json.dumps(trace), encoding="utf-8")
                    (report_dir / "1001.manifest.json").write_text(json.dumps({"git_revision": "frozen-revision"}), encoding="utf-8")
            report = audit_v4_preflight(root=root, run_id=run_id, replicate_id=1001, samples=20)
        self.assertEqual(report["status"], "engineering_ready_for_formal_run")
        self.assertEqual(report["observed_unit_count"], 70)
        self.assertEqual(report["issues"], [])

    def test_v4_raw_text_baselines_have_fidelity_boundaries(self):
        for system_id, scenario_id, expected_source in [
            ("B1", "H2_v4_behavioral", "raw_text_rag"),
            ("B4", "B6_v4_behavioral", "full_raw_history"),
        ]:
            scenario = load_scenario(Path(f"experiments/scenarios/protocol_v4/{scenario_id.split('_v4_')[0]}.yaml"))
            config = build_system_registry()[system_id]
            config.evaluation_protocol = "v4"
            trace = run_agent_scenario(scenario, seed=1001, system_config=config)
            self.assertEqual(trace["evaluation_protocol"], "v4")
            self.assertEqual(trace["steps"][0]["retrieval_metadata"]["baseline_context_source"], expected_source)
            self.assertEqual(trace["memory_records_after"], [])
            self.assertEqual(trace["system_configuration"]["system_id"], system_id)

    def test_mechanism_activation_audit_requires_disabled_invocation_and_isolation(self):
        registry = build_system_registry()
        for system_id, mechanism, allowed in [
            ("-Decay", "dynamic_confidence", {"use_dynamic_confidence"}),
            ("-AsymFeedback", "asym_feedback", {"alpha_neg"}),
            ("-Governance", "governance", {"use_governance", "use_resampling", "use_content_aging"}),
            ("-CandidateGate", "candidate_gate", {"use_candidate_gate"}),
            ("-ConflictHandling", "conflict_handling", {"use_conflict_handling"}),
            ("-FeatureAbsorption", "feature_absorption", {"use_feature_absorption"}),
            ("-Ripple", "ripple", {"use_ripple"}),
            ("-Split", "split", {"use_split"}),
        ]:
            config = registry[system_id].__dict__
            trace = {
                "system_configuration": config,
                "mechanism_activation": [{"mechanism": mechanism, "enabled": False, "event": "smoke"}],
            }
            report = audit_activation(trace, system_id)
            self.assertEqual(report["status"], "pass", (system_id, report))
            self.assertEqual(set(report["unexpected_config_differences"]), set())
            self.assertTrue(allowed)

    def test_configured_baselines_script_outputs(self):
        env = dict(os.environ)
        env["MAX_PRIMARY_SEEDS"] = "1"
        subprocess.run(
            [sys.executable, "experiments/scripts/run_configured_baselines.py"],
            cwd=Path.cwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        metrics_path = Path("experiments/results/aggregated_metrics/configured_baseline_dev/B0/oracle/metrics.summary.json")
        self.assertTrue(metrics_path.exists())

    def test_configured_ablations_script_outputs(self):
        env = dict(os.environ)
        env["MAX_SECONDARY_SEEDS"] = "1"
        subprocess.run(
            [sys.executable, "experiments/scripts/run_configured_ablations.py"],
            cwd=Path.cwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        metrics_path = Path("experiments/results/aggregated_metrics/configured_ablation_dev/-Decay/oracle/metrics.summary.json")
        self.assertTrue(metrics_path.exists())

    def test_run_all_configured_smoke(self):
        env = dict(os.environ)
        env["MAX_PRIMARY_SEEDS"] = "1"
        env["MAX_SECONDARY_SEEDS"] = "1"
        subprocess.run(
            [sys.executable, "experiments/scripts/run_all_configured.py"],
            cwd=Path.cwd(),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(Path("experiments/results/reports/configured_oracle_dev/Ours/oracle/manifest.json").exists())
        self.assertTrue(Path("experiments/results/reports/configured_baseline_dev/B0/oracle/manifest.json").exists())
        self.assertTrue(Path("experiments/results/reports/configured_ablation_dev/-Decay/oracle/manifest.json").exists())
        self.assertTrue(Path("docs/实验结果摘要.md").exists())
        self.assertTrue(Path("experiments/results/reports/dev/statistics_summary.json").exists())
        self.assertTrue(Path("experiments/results/reports/dev/significance_summary.json").exists())
        self.assertTrue(Path("experiments/results/reports/dev/run_index.json").exists())
        self.assertGreater(Path("experiments/results/tables/dev/table_1.csv").stat().st_size, 20)
        self.assertGreater(Path("experiments/results/tables/dev/table_2.csv").stat().st_size, 20)
        audit = json.loads(Path("experiments/results/reports/dev/artifact_audit.json").read_text(encoding="utf-8"))
        self.assertEqual(audit["status"], "pass")
        significance = json.loads(
            Path("experiments/results/reports/dev/significance_summary.json").read_text(encoding="utf-8")
        )
        self.assertTrue(all(row["sampling_unit"] == "scenario" for row in significance))
        annotation = json.loads(Path("experiments/annotations/annotation_agreement.json").read_text(encoding="utf-8"))
        self.assertEqual(annotation["status"], "pending_human_annotation")
        self.assertIsNone(annotation["cohen_kappa"])

    def test_strict_experiment_matrix_script_outputs(self):
        output = Path(tempfile.gettempdir()) / "strict_experiment_matrix_test.json"
        if output.exists():
            output.unlink()
        subprocess.run(
            [sys.executable, "experiments/scripts/build_strict_experiment_matrix.py", "--output", str(output)],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        matrix = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(matrix["scenario_count"], 36)
        self.assertEqual(matrix["summary"]["main_agent_total_units"], 7 * 36 * 30)
        self.assertEqual(matrix["summary"]["oracle_ablation_total_units"], 8 * 36 * 20)
        self.assertEqual(matrix["summary"]["overall_total_units"], (7 * 36 * 30) + (8 * 36 * 20))

    def test_protocol_v4_assets_are_isolated_and_serial_runner_accepts_them(self):
        matrix_path = Path(tempfile.gettempdir()) / "protocol_v4_pilot_matrix_test.json"
        annotation_root = Path(tempfile.gettempdir()) / "protocol_v4_annotations_test"
        results_root = Path(tempfile.gettempdir()) / "protocol_v4_serial_smoke"
        for path in (matrix_path,):
            if path.exists():
                path.unlink()
        for path in (annotation_root, results_root):
            if path.exists():
                import shutil

                shutil.rmtree(path)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/build_protocol_v4_pilot_matrix.py",
                "--output",
                str(matrix_path),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
        self.assertEqual(matrix["unit_count"], 42)
        self.assertTrue(all("source_planner_mode" in unit for unit in matrix["units"]))
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/sync_ground_truth.py",
                "--protocol",
                "v4",
                "--output-root",
                str(annotation_root),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(len(list((annotation_root / "inter_annotator").glob("*.json"))), 13)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--matrix",
                str(matrix_path),
                "--results-root",
                str(results_root),
                "--run-id",
                "protocol_v4_smoke",
                "--group-id",
                "protocol_v4_agent_pilot",
                "--system-id",
                "B1",
                "--scenario-id",
                "H2_v4_behavioral",
                "--seed",
                "1001",
                "--planner-mode",
                "agent",
                "--agent-backend",
                "heuristic",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        manifest_path = results_root / "reports" / "protocol_v4_smoke" / "B1" / "agent" / "H2_v4_behavioral" / "1001.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["evaluation_protocol"], "v4")
        self.assertEqual(manifest["baseline_context_source"], "raw_text_rag")
        self.assertIn("v4_external_result_recorded", manifest["strict_checks"])

    def test_protocol_v4_formal_assets_define_resumable_complete_matrices(self):
        output_dir = Path(tempfile.gettempdir()) / "protocol_v4_formal_assets_test"
        if output_dir.exists():
            import shutil

            shutil.rmtree(output_dir)
        subprocess.run(
            [sys.executable, "experiments/scripts/build_protocol_v4_formal_assets.py", "--output-dir", str(output_dir)],
            cwd=Path.cwd(), check=True, capture_output=True, text=True,
        )
        main = json.loads((output_dir / "protocol_v4_formal_agent_matrix.json").read_text(encoding="utf-8"))
        longitudinal = json.loads((output_dir / "protocol_v4_formal_longitudinal_matrix.json").read_text(encoding="utf-8"))
        robustness = json.loads((output_dir / "protocol_v4_formal_robustness_matrix.json").read_text(encoding="utf-8"))
        self.assertEqual(main["unit_count"], 7 * 10 * 30)
        self.assertEqual(longitudinal["unit_count"], 7 * 1 * 30)
        self.assertEqual(robustness["unit_count"], 7 * 2 * 2)
        self.assertTrue(all(unit["replicate_id"] == unit["seed"] for unit in main["units"]))
        self.assertTrue(main["requirements"]["resume_supported"])
        self.assertEqual(main["seed_protocol"], "replicate_id")

    def test_artifact_bundle_records_included_protocol_assets(self):
        output = Path(tempfile.gettempdir()) / "protocol_v4_artifact_bundle_test"
        if output.exists():
            import shutil

            shutil.rmtree(output)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/build_artifact_bundle.py",
                "--matrix",
                "experiments/configs/protocol_v4_pilot_matrix.json",
                "--output",
                str(output),
                "--include",
                "experiments/scenarios/protocol_v4/H2.yaml",
                "experiments/world_model/v1.json",
                "experiments/runner/system_registry.py",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        manifest = json.loads((output / "artifact_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("working_tree_clean", manifest)
        self.assertEqual(len(manifest["files"]), 4)

    def test_protocol_v4_readiness_audit_reports_transport_blocker(self):
        root = Path(tempfile.gettempdir()) / "protocol_v4_readiness_pilot"
        if root.exists():
            import shutil

            shutil.rmtree(root)
        trace_path = root / "raw_traces" / "pilot" / "Ours" / "agent" / "H2_v4_behavioral" / "1001.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps({"agent_backend": "external_llm", "agent_failures": ["external_call_failed:RuntimeError"]}),
            encoding="utf-8",
        )
        output = root / "readiness.json"
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_protocol_v4_readiness.py",
                "--pilot-root",
                str(root),
                "--pilot-run-id",
                "pilot",
                "--output",
                str(output),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "blocked_on_llm_authorization")
        self.assertEqual(report["valid_external_trace_count"], 0)

    def test_protocol_v4_readiness_records_seed_probe_failures(self):
        root = Path(tempfile.gettempdir()) / "protocol_v4_readiness_probe_audit"
        if root.exists():
            import shutil

            shutil.rmtree(root)
        probe = root / "reports" / "probe_retry" / "external_llm_seed_probe.json"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(
            json.dumps(
                {
                    "probe_status": "error",
                    "failure_type": "RuntimeError",
                    "failure_message": "http_error:503:proxy_unavailable",
                }
            ),
            encoding="utf-8",
        )
        output = root / "readiness.json"
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_protocol_v4_readiness.py",
                "--pilot-root",
                str(root),
                "--pilot-run-id",
                "pilot",
                "--output",
                str(output),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["seed_probe_failure_count"], 1)
        self.assertIn("503", report["seed_probe_failures"][0]["failure_message"])

    def test_protocol_v4_readiness_accepts_successful_seed_probe(self):
        root = Path(tempfile.gettempdir()) / "protocol_v4_readiness_probe_success"
        if root.exists():
            import shutil

            shutil.rmtree(root)
        probe = root / "reports" / "probe_ok" / "external_llm_seed_probe.json"
        probe.parent.mkdir(parents=True, exist_ok=True)
        probe.write_text(json.dumps({"probe_status": "ok"}), encoding="utf-8")
        output = root / "readiness.json"
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_protocol_v4_readiness.py",
                "--pilot-root",
                str(root),
                "--pilot-run-id",
                "pilot",
                "--output",
                str(output),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["seed_probe_success_count"], 1)

    def test_protocol_v4_annotation_requires_adjudication_for_disagreements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "annotations"
            root.mkdir()
            path = root / "S1.json"
            path.write_text(
                json.dumps(
                    {
                        "scenario_id": "S1",
                        "annotator_a": {"safe": True},
                        "annotator_b": {"safe": False},
                        "adjudication": {
                            "status": "not_required_yet",
                            "adjudicator_id": None,
                            "final_label": None,
                            "rationale": None,
                        },
                    }
                ),
                encoding="utf-8",
            )
            report = compute_annotation_agreement(root, Path(tmp) / "agreement.json")
            self.assertEqual(report["status"], "pending_adjudication")
            self.assertEqual(report["adjudication_required_count"], 1)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["adjudication"] = {
                "status": "complete",
                "adjudicator_id": "human_c",
                "final_label": {"safe": True},
                "rationale": "按场景安全约束裁决。",
            }
            path.write_text(json.dumps(payload), encoding="utf-8")
            report = compute_annotation_agreement(root, Path(tmp) / "agreement.json")
            self.assertEqual(report["status"], "complete")
            self.assertEqual(report["adjudication_required_count"], 0)

    def test_protocol_v4_readiness_requires_annotation_preflight_and_freeze(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            annotation = root / "annotation.json"
            preflight = root / "preflight.json"
            freeze = root / "freeze.json"
            annotation.write_text(
                json.dumps(
                    {
                        "status": "pending_human_annotation",
                        "pending_count": 13,
                        "adjudication_required_count": 0,
                        "cohen_kappa": None,
                    }
                ),
                encoding="utf-8",
            )
            preflight.write_text(json.dumps({"status": "engineering_ready_for_formal_run"}), encoding="utf-8")
            freeze.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
            report = audit_v4_readiness(
                pilot_root=root,
                pilot_run_id="pilot",
                annotation_report=annotation,
                preflight_audit=preflight,
                freeze_audit=freeze,
            )
            self.assertEqual(report["status"], "engineering_ready_but_annotation_blocked")
            annotation.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "pending_count": 0,
                        "adjudication_required_count": 0,
                        "cohen_kappa": None,
                    }
                ),
                encoding="utf-8",
            )
            report = audit_v4_readiness(
                pilot_root=root,
                pilot_run_id="pilot",
                annotation_report=annotation,
                preflight_audit=preflight,
                freeze_audit=freeze,
            )
            self.assertEqual(report["status"], "engineering_ready_but_annotation_blocked")
            annotation.write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "pending_count": 0,
                        "adjudication_required_count": 0,
                        "cohen_kappa": 0.8,
                    }
                ),
                encoding="utf-8",
            )
            report = audit_v4_readiness(
                pilot_root=root,
                pilot_run_id="pilot",
                annotation_report=annotation,
                preflight_audit=preflight,
                freeze_audit=freeze,
            )
            self.assertEqual(report["status"], "engineering_ready_for_formal_run")
            self.assertEqual(report["formal_run_blockers"], [])

    def test_protocol_v4_trace_audit_exits_nonzero_on_empty_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = subprocess.run(
                [
                    sys.executable,
                    "experiments/scripts/audit_protocol_v4_traces.py",
                    "--root",
                    str(root),
                    "--output",
                    str(root / "trace_audit.json"),
                ],
                cwd=Path.cwd(),
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 2)
            report = json.loads((root / "trace_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "fail")

    def test_llm_assisted_annotation_requires_explicit_readiness_opt_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenarios = {
                f"scenario_{index}": {
                    "expected_actions": [],
                    "expected_action": None,
                    "expected_final_state": None,
                    "final_state_applicable": False,
                    "expected_clarify": bool(index % 2),
                    "expected_no_action": True,
                    "rationale": "independent",
                    "confidence": 0.9,
                }
                for index in range(13)
            }
            annotator_a = root / "a.json"
            annotator_b = root / "b.json"
            adjudicator = root / "c.json"
            annotator_a.write_text(json.dumps({"annotation_type": "independent_llm_annotation", "annotator_id": "a", "scenarios": scenarios}), encoding="utf-8")
            annotator_b.write_text(json.dumps({"annotation_type": "independent_llm_annotation", "annotator_id": "b", "scenarios": scenarios}), encoding="utf-8")
            adjudicator.write_text(
                json.dumps(
                    {
                        "annotation_type": "independent_llm_adjudication",
                        "adjudicator_id": "c",
                        "scenarios": {
                            scenario_id: {
                                "annotator_a_agrees": True,
                                "annotator_b_agrees": True,
                                "resolution_type": "agreement",
                                "final_label": {field: row[field] for field in [
                                    "expected_actions", "expected_action", "expected_final_state",
                                    "final_state_applicable", "expected_clarify", "expected_no_action",
                                ]},
                                "rationale": "agreed",
                            }
                            for scenario_id, row in scenarios.items()
                        },
                    }
                ),
                encoding="utf-8",
            )
            annotation_report = audit_llm_assisted_annotation(
                annotator_a_path=annotator_a,
                annotator_b_path=annotator_b,
                adjudicator_path=adjudicator,
                researcher_accepted_provisional=True,
            )
            annotation_path = root / "annotation.json"
            annotation_path.write_text(json.dumps(annotation_report), encoding="utf-8")
            preflight = root / "preflight.json"
            freeze = root / "freeze.json"
            preflight.write_text(json.dumps({"status": "engineering_ready_for_formal_run"}), encoding="utf-8")
            freeze.write_text(json.dumps({"status": "pass"}), encoding="utf-8")
            blocked = audit_v4_readiness(
                pilot_root=root,
                pilot_run_id="pilot",
                annotation_report=annotation_path,
                preflight_audit=preflight,
                freeze_audit=freeze,
            )
            self.assertEqual(blocked["status"], "engineering_ready_but_annotation_blocked")
            allowed = audit_v4_readiness(
                pilot_root=root,
                pilot_run_id="pilot",
                annotation_report=annotation_path,
                preflight_audit=preflight,
                freeze_audit=freeze,
                allow_llm_assisted_annotation=True,
            )
            self.assertEqual(allowed["status"], "engineering_ready_for_formal_run")
            self.assertTrue(allowed["checks"]["llm_assisted_annotation_used"])

    def test_protocol_v4_cost_estimator_uses_only_valid_external_traces(self):
        root = Path(tempfile.gettempdir()) / "protocol_v4_cost_estimator"
        if root.exists():
            import shutil

            shutil.rmtree(root)
        trace = root / "raw_traces" / "run" / "Ours" / "agent" / "H2" / "1001.json"
        trace.parent.mkdir(parents=True, exist_ok=True)
        trace.write_text(
            json.dumps(
                {
                    "agent_backend": "external_llm",
                    "agent_failures": [],
                    "scenario_id": "H2",
                    "system_id": "Ours",
                    "seed": 1001,
                    "external_task_success": True,
                    "agent_usage_metadata": [{"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13}],
                }
            ),
            encoding="utf-8",
        )
        output = root / "cost.json"
        subprocess.run(
            [sys.executable, "experiments/scripts/estimate_protocol_v4_cost.py", "--pilot-root", str(root), "--output", str(output)],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(report["observed_valid_external_units"], 1)
        self.assertEqual(report["observed_rows"][0]["total_tokens"], 13)

    def test_strict_group_runner_forwards_selected_matrix_to_units(self):
        source = Path("experiments/scripts/run_strict_group.py").read_text(encoding="utf-8")
        self.assertIn('"--matrix",', source)
        self.assertIn("matrix_path=Path(args.matrix)", source)

    def test_strict_group_runner_retries_timeout_from_agent_failure_field(self):
        from experiments.scripts.run_strict_group import _is_retryable_transport_failure

        self.assertTrue(_is_retryable_transport_failure({
            "status": "strict_check_failed", "agent_failures": ["external_call_failed:timeout"],
            "agent_raw_output_excerpt": '{"actions":[]}',
        }))

    def test_protocol_v4_trace_tools_are_directly_executable(self):
        for name in ("audit_protocol_v4_traces.py", "aggregate_protocol_v4_pilot.py"):
            source = Path("experiments/scripts") / name
            self.assertIn("sys.path.insert", source.read_text(encoding="utf-8"))

    def test_raw_text_ingestion_supersedes_prior_preference_in_sqlite(self):
        db_path = Path(tempfile.gettempdir()) / "protocol_v4_text_ingestion.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = datetime.now().astimezone()
        first = ingest_user_text(service, text="我喜欢把卧室空调设为24度", now=now, turn_id="u1")
        second = ingest_user_text(service, text="不对，我喜欢把卧室空调改成26度", now=now + timedelta(days=1), turn_id="u2")
        records = service.list_records(include_deleted=True)
        self.assertTrue(first["accepted"])
        self.assertTrue(second["accepted"])
        self.assertEqual(second["replaced_memory_id"], first["memory_id"])
        self.assertEqual(len(records), 2)
        self.assertEqual(next(item for item in records if item.memory_id == first["memory_id"]).status, "superseded")
        self.assertEqual(next(item for item in records if item.memory_id == second["memory_id"]).object, "26")

    def test_raw_text_ingestion_rejects_negated_and_ambiguous_room_text(self):
        db_path = Path(tempfile.gettempdir()) / "protocol_v4_text_ingestion_boundary.sqlite3"
        if db_path.exists():
            db_path.unlink()
        service = MemoryService(db_path)
        now = datetime.now().astimezone()
        negated = ingest_user_text(service, text="我不喜欢把卧室空调设为26度", now=now, turn_id="negated")
        ambiguous = ingest_user_text(service, text="我喜欢把空调设为26度", now=now, turn_id="ambiguous")
        self.assertFalse(negated["accepted"])
        self.assertEqual(negated["reason"], "negated_preference_requires_clarification")
        self.assertFalse(ambiguous["accepted"])
        self.assertEqual(service.list_records(include_deleted=True), [])

    def test_integrated_replay_maps_planner_select_to_executable_action(self):
        source = Path("experiments/scripts/run_protocol_v4_integrated_replay.py").read_text(encoding="utf-8")
        self.assertIn("_infer_control_action", source)
        self.assertIn('actions[0].get("service") == "planner.select"', source)

    def test_v41_supplemental_protocol_is_preregistered_and_isolated(self):
        config = json.loads(Path("experiments/configs/protocol_v4_1_supplemental_ingestion.json").read_text(encoding="utf-8"))
        self.assertEqual(config["status"], "preregistered_before_execution")
        self.assertEqual(config["systems"], ["Ours", "B0", "B1", "B2", "B3", "B4", "B5"])
        self.assertEqual(len(config["trajectories"]), 3)
        self.assertEqual(len(config["replicate_ids"]), 10)
        self.assertIn("memory_ops", config["requirements"]["forbidden_runtime_inputs"])

    def test_v41_supplemental_runner_has_no_gold_bridge_or_heuristic_backend(self):
        source = Path("experiments/scripts/run_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn('choices=["external"]', source)
        self.assertIn('"forbidden_runtime_inputs_absent": True', source)
        self.assertNotIn("action_template", source)

    def test_v41_supplemental_audit_checks_baseline_fidelity_and_usage(self):
        source = Path("experiments/scripts/audit_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("b1_fidelity", source)
        self.assertIn("b4_fidelity", source)
        self.assertIn("usage_missing", source)
        self.assertIn("revision_mismatch", source)

    def test_v41_supplemental_transport_retry_is_bounded_and_preserved(self):
        config = json.loads(Path("experiments/configs/protocol_v4_1_supplemental_ingestion.json").read_text(encoding="utf-8"))
        source = Path("experiments/scripts/run_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertEqual(config["requirements"]["max_transport_retries"], 1)
        self.assertIn("transport_attempts", source)
        self.assertIn("max_transport_retries + 2", source)

    def test_v41_supplemental_repair_preserves_transport_failure(self):
        source = Path("experiments/scripts/run_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        audit = Path("experiments/scripts/audit_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("repair_attempts", source)
        self.assertIn("transport_repair", source)
        self.assertIn("repair_provenance_missing", audit)

    def test_v41_supplemental_manifest_accumulates_executed_replicates(self):
        source = Path("experiments/scripts/run_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        audit = Path("experiments/scripts/audit_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("executed_replicates", source)
        self.assertIn("preregistered_replicates", source)
        self.assertIn("different frozen revision or protocol", source)
        self.assertIn('freeze.get("executed_replicates", [])', audit)

    def test_v41_supplemental_seal_uses_sha256(self):
        source = Path("experiments/scripts/seal_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("hashlib.sha256", source)
        self.assertIn("file_count", source)

    def test_v41_supplemental_runner_persists_unexpected_unit_exceptions(self):
        source = Path("experiments/scripts/run_protocol_v41_supplemental.py").read_text(encoding="utf-8")
        self.assertIn("runner_exception", source)
        self.assertIn("traceback.format_exc", source)

    def test_control_inference_uses_unique_usable_temperature_memory(self):
        world = HAOracle()
        package = SearchResultPackage(
            query="按我喜欢的温度设卧室空调",
            candidate_devices=[CandidateDevice(entity_id="climate.bedroom_ac", name="卧室空调", score=0.9, confidence=0.9)],
            matched_memories=[MatchedMemory(
                memory_id="active_pref", memory_type="preference", text="用户喜欢把卧室空调设为26度",
                score=0.9, raw_confidence=0.9, effective_confidence=0.9, memory_worth=0.8,
                system_status="active", true_status="active", runtime_status="active", layer="active", in_usable_set=True,
            )],
        )
        action = _infer_control_action(package.query, package, world)
        self.assertEqual(action["args"].get("temperature"), 26)

    def test_longitudinal_audit_declares_persistence_and_raw_history_boundaries(self):
        source = Path("experiments/scripts/audit_protocol_v4_longitudinal.py").read_text(encoding="utf-8")
        self.assertIn("sqlite_persists_across_session", source)
        self.assertIn("b4_context_grows_beyond_b1", source)
        self.assertIn("b4_uses_structured_memory\": False", source)

    def test_v4_annotation_assets_preserve_missing_and_adjudication_fields(self):
        source = Path("experiments/scripts/sync_ground_truth.py").read_text(encoding="utf-8")
        agreement = Path("experiments/scripts/compute_annotation_agreement.py").read_text(encoding="utf-8")
        self.assertIn("missing_value_policy", source)
        self.assertIn("adjudicator_id", source)
        self.assertIn("adjudication_required_count", agreement)

    def test_v4_artifact_rebuild_entrypoint_exists(self):
        source = Path("experiments/scripts/generate_v4_artifacts.py").read_text(encoding="utf-8")
        self.assertIn("build_artifact_bundle.py", source)
        self.assertIn("sync_ground_truth.py", source)

    def test_strict_serial_oracle_unit_and_partial_audit(self):
        results_root = Path(tempfile.gettempdir()) / "strict_serial_oracle_smoke"
        if results_root.exists():
            import shutil

            shutil.rmtree(results_root)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_oracle_smoke",
                "--group-id",
                "strict_oracle_ablations",
                "--system-id=-Decay",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "oracle",
                "--results-root",
                str(results_root),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        resume = subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_oracle_smoke",
                "--group-id",
                "strict_oracle_ablations",
                "--system-id=-Decay",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "oracle",
                "--results-root",
                str(results_root),
                "--resume",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("skipped_existing", resume.stdout)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_strict_experiment.py",
                "--run-id",
                "strict_oracle_smoke",
                "--group-id",
                "strict_oracle_ablations",
                "--results-root",
                str(results_root),
                "--allow-partial",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        audit = json.loads(
            (results_root / "reports" / "strict_oracle_smoke" / "strict_oracle_ablations.strict_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["status"], "partial")
        self.assertEqual(audit["observed_unit_count"], 1)

    def test_strict_serial_resume_repairs_incomplete_artifacts(self):
        results_root = Path(tempfile.gettempdir()) / "strict_serial_resume_repair"
        if results_root.exists():
            import shutil

            shutil.rmtree(results_root)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_resume_repair",
                "--group-id",
                "strict_oracle_ablations",
                "--system-id=-Decay",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "oracle",
                "--results-root",
                str(results_root),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        maintenance_path = (
            results_root / "raw_traces" / "strict_resume_repair" / "-Decay" / "oracle" / "A1" / "1001.maintenance.json"
        )
        maintenance_path.write_text("{", encoding="utf-8")
        resume = subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_resume_repair",
                "--group-id",
                "strict_oracle_ablations",
                "--system-id=-Decay",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "oracle",
                "--results-root",
                str(results_root),
                "--resume",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("resume_repair_required", resume.stdout)
        repaired = json.loads(maintenance_path.read_text(encoding="utf-8"))
        self.assertIsInstance(repaired["maintenance_events"], list)

    def test_strict_serial_agent_external_requirement_rejects_fallback(self):
        results_root = Path(tempfile.gettempdir()) / "strict_serial_agent_smoke"
        if results_root.exists():
            import shutil

            shutil.rmtree(results_root)
        result = subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_agent_smoke",
                "--group-id",
                "strict_main_agent",
                "--system-id",
                "Ours",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "agent",
                "--results-root",
                str(results_root),
                "--require-agent-backend",
                "external_llm",
            ],
            cwd=Path.cwd(),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 3)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_strict_experiment.py",
                "--run-id",
                "strict_agent_smoke",
                "--group-id",
                "strict_main_agent",
                "--results-root",
                str(results_root),
                "--allow-partial",
            ],
            cwd=Path.cwd(),
            check=False,
            capture_output=True,
            text=True,
        )
        audit = json.loads(
            (results_root / "reports" / "strict_agent_smoke" / "strict_main_agent.strict_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["status"], "fail")
        self.assertTrue(any(item["code"] == "heuristic_fallback_detected" for item in audit["failures"]))

    def test_strict_group_runner_smoke(self):
        results_root = Path(tempfile.gettempdir()) / "strict_group_oracle_smoke"
        if results_root.exists():
            import shutil

            shutil.rmtree(results_root)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_group.py",
                "--run-id",
                "strict_group_oracle_smoke",
                "--group-id",
                "strict_oracle_ablations",
                "--results-root",
                str(results_root),
                "--systems=-Decay",
                "--scenarios",
                "A1,A2",
                "--seeds",
                "1001",
                "--max-concurrency",
                "2",
                "--resume",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        summary = json.loads(
            (results_root / "reports" / "strict_group_oracle_smoke" / "strict_oracle_ablations.group_run_summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(summary["completed_count"], 2)
        self.assertEqual(summary["failure_count"], 0)
        self.assertEqual(summary["peak_concurrency"], 2)
        self.assertEqual(summary["attempt_count"], 2)

    def test_strict_audit_and_cost_normalize_prompt_completion_tokens(self):
        results_root = Path(tempfile.gettempdir()) / "strict_group_agent_normalized"
        if results_root.exists():
            import shutil

            shutil.rmtree(results_root)
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/run_strict_serial_unit.py",
                "--run-id",
                "strict_group_agent_normalized",
                "--group-id",
                "strict_main_agent",
                "--system-id",
                "Ours",
                "--scenario-id",
                "A1",
                "--seed",
                "1001",
                "--planner-mode",
                "agent",
                "--results-root",
                str(results_root),
                "--resume",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        manifest_path = results_root / "reports" / "strict_group_agent_normalized" / "Ours" / "agent" / "A1" / "1001.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        trace_path = results_root / manifest["trace_file"]
        trace = json.loads(trace_path.read_text(encoding="utf-8"))
        trace["agent_usage_metadata"] = [
            {"step_id": "stub", "prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
        ]
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/audit_strict_experiment.py",
                "--run-id",
                "strict_group_agent_normalized",
                "--group-id",
                "strict_main_agent",
                "--results-root",
                str(results_root),
                "--allow-partial",
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                sys.executable,
                "experiments/scripts/estimate_strict_main_cost.py",
                "--run-id",
                "strict_group_agent_normalized",
                "--group-id",
                "strict_main_agent",
                "--results-root",
                str(results_root),
                "--audit",
                str(results_root / "reports" / "strict_group_agent_normalized" / "strict_main_agent.strict_audit.json"),
            ],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
        audit = json.loads(
            (results_root / "reports" / "strict_group_agent_normalized" / "strict_main_agent.strict_audit.json").read_text(
                encoding="utf-8"
            )
        )
        estimate = json.loads(
            (results_root / "reports" / "strict_group_agent_normalized" / "strict_main_agent.cost_estimate.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(audit["usage_summary"]["agent_prompt_tokens"], 10)
        self.assertEqual(audit["usage_summary"]["agent_completion_tokens"], 2)
        self.assertEqual(audit["usage_summary"]["agent_total_tokens"], 12)
        self.assertEqual(estimate["extrapolation"]["mean_prompt_tokens_per_unit"], 10.0)
        self.assertEqual(estimate["extrapolation"]["mean_completion_tokens_per_unit"], 2.0)

    def test_finalize_protocol_v4_formal_writes_hermetic_artifacts(self):
        metrics = {
            metric: {"mean": (0.75 if metric == "TSR" else 0.0), "eligible_count": 1}
            for metric in (
                "TSR",
                "Control Final-State TSR",
                "Query Answer Accuracy",
                "Automation Decision Accuracy",
                "WDR",
                "Unsafe Action Rate",
                "Necessary Clarification Rate",
                "Unnecessary Clarification Rate",
                "Prompt Tokens",
                "Completion Tokens",
                "end_to_end_latency_ms",
                "SRR",
                "UC",
            )
        }
        workload = {
            "workload": "behavioral",
            "unit_count": 1,
            "systems": [{"system_id": "Ours", "unit_count": 1, "metrics": metrics}],
            "failure_counts": [],
        }
        report = {
            "git_revision": "test-revision",
            "workloads": [workload],
            "behavioral_tsr_comparisons": [
                {
                    "baseline": "B0",
                    "ours_minus_baseline": 0.5,
                    "ci_low": 0.1,
                    "ci_high": 0.8,
                    "holm_adjusted_p": 0.01,
                    "paired_eligible_count": 1,
                }
            ],
            "transport_repairs": {
                "repair_unit_count": 1,
                "partial_api_calls": 0,
                "partial_total_tokens": 0,
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            csv_path = root / "behavioral.csv"
            markdown_path = root / "summary.md"
            _write_csv(csv_path, workload)
            _write_markdown(markdown_path, report)
            csv_text = csv_path.read_text(encoding="utf-8")
            markdown = markdown_path.read_text(encoding="utf-8")
        self.assertIn("system_id,unit_count,TSR", csv_text)
        self.assertIn("Ours,1,0.75", csv_text)
        self.assertIn("complete_llm_assisted", markdown)
        self.assertIn("plan-only Agent adapter", markdown)
        self.assertIn("| B0 | 0.5000 | [0.1000, 0.8000] | 0.01 | 1 |", markdown)


if __name__ == "__main__":
    unittest.main()
