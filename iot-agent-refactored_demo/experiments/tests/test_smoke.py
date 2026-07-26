from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from unittest import mock

from experiments.memory.schemas import CandidateDevice, SearchResultPackage, UsageEvent
from experiments.memory.service import MemoryService
from experiments.metrics.core import aggregate_task_metrics, task_metrics
from experiments.planners.agent_planner import AgentPlanner, AgentPlannerDecision, ExternalLLMClient, _build_plan_prompt
from experiments.runner.batch_run import run_batch, run_batch_multi_seed
from experiments.runner.scenario_loader import load_scenario
from experiments.runner.single_run import run_agent_scenario
from experiments.world_model.ha_oracle import HAOracle


class WorldModelTest(unittest.TestCase):
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


class MemoryServiceTest(unittest.TestCase):
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

    def test_external_llm_parse_failure_falls_back_with_failure_type(self):
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
        self.assertEqual(decision.backend, "heuristic_fallback")
        self.assertTrue(decision.failure_type.startswith("external_parse_failed"))
        self.assertEqual(decision.action["service"], "planner.select")

    def test_external_llm_call_failure_falls_back_with_failure_type(self):
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
        self.assertEqual(decision.backend, "heuristic_fallback")
        self.assertEqual(decision.failure_type, "external_call_failed:RuntimeError")

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
        ):
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
        ):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                client = ExternalLLMClient()
                response = client.invoke("测试 prompt", requested_seed=1002)
        self.assertEqual(captured["body"]["seed"], 1002)
        self.assertEqual(response["requested_seed"], 1002)
        self.assertTrue(response["request_seed_supported"])
        self.assertTrue(response["request_seed_applied"])
        self.assertEqual(response["seed_protocol"], "provider_seed")
        self.assertEqual(response["response_metadata"]["system_fingerprint"], "fp-stub")

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
        report = Path("docs/实验结果摘要.md")
        self.assertTrue(report.exists())
        text = report.read_text(encoding="utf-8")
        self.assertIn("实验结果摘要", text)
        self.assertIn("Ours 结果概览", text)
        self.assertIn("TSR_cohen_d", text)

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


if __name__ == "__main__":
    unittest.main()
