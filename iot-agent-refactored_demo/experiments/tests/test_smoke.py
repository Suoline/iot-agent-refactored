from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
import subprocess
import sys

from experiments.memory.schemas import UsageEvent
from experiments.memory.service import MemoryService
from experiments.runner.batch_run import run_batch, run_batch_multi_seed
from experiments.runner.scenario_loader import load_scenario
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


class RunnerSmokeTest(unittest.TestCase):
    def test_batch_run_generates_metrics(self):
        scenario = Path("experiments/scenarios/category_a/A1.yaml")
        loaded = load_scenario(scenario)
        self.assertEqual(loaded["scenario_id"], "A1")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"))
        self.assertIn("TSR", result["metrics"])

    def test_registry_fallback_for_archived_alias(self):
        scenario = Path("experiments/scenarios/category_a/A6.yaml")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), run_id="test_fallback")
        self.assertIn("TSR", result["metrics"])
        self.assertGreaterEqual(result["metrics"]["TSR"], 1.0)

    def test_candidate_promotion_allows_control(self):
        scenario = Path("experiments/scenarios/category_b/B2.yaml")
        result = run_batch([scenario], seed=1001, results_root=Path("experiments/results"), run_id="test_b2")
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

    def test_prompt_tokens_nonzero_in_main_run(self):
        result = run_batch(
            [Path("experiments/scenarios/category_a/A1.yaml")],
            seed=1001,
            results_root=Path("experiments/results"),
            run_id="test_prompt_tokens",
        )
        self.assertGreater(result["metrics"]["prompt_tokens"], 0.0)

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
        self.assertTrue(Path("experiments/results/reports/dev/run_index.json").exists())


if __name__ == "__main__":
    unittest.main()
