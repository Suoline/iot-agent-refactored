from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class RetrievedMemoryTrace(BaseModel):
    memory_id: str
    memory_type: str
    rank: int
    retrieval_score: float
    effective_confidence: float
    memory_worth: float
    raw_confidence: float
    system_status: str
    true_status: str
    runtime_status: str
    in_usable_set: bool
    in_grounding_set: bool = False


class RetrievalStepTrace(BaseModel):
    step_id: str
    stage: str
    query: str
    retrieved_memories: list[RetrievedMemoryTrace] = Field(default_factory=list)
    retrieval_latency_ms: float = 0.0


class MaintenanceTrace(BaseModel):
    maintenance_id: str
    sim_time: datetime
    expired_memory_ids: list[str] = Field(default_factory=list)
    stale_memory_ids: list[str] = Field(default_factory=list)
    archived_memory_ids: list[str] = Field(default_factory=list)
    resampled_memory_ids: list[str] = Field(default_factory=list)
    rollback_merge_ids: list[str] = Field(default_factory=list)
    needs_review_ids: list[str] = Field(default_factory=list)
    deleted_by_capacity_ids: list[str] = Field(default_factory=list)
    maintenance_latency_ms: float = 0.0
    estimated_maintenance_tokens: int = 0


class TaskTrace(BaseModel):
    task_id: str
    scenario_id: str
    seed: int
    agent_requested_seed: Optional[int] = None
    agent_request_seed_supported: Optional[bool] = None
    agent_request_seed_applied: Optional[bool] = None
    agent_seed_protocol: Optional[str] = None
    world_version: str
    system_policy_version: str = "sp-v1"
    planner_mode: str
    agent_backend: Optional[str] = None
    agent_raw_outputs: list[str] = Field(default_factory=list)
    system_id: str
    task_type: str = "control"
    sim_time: datetime
    steps: list[RetrievalStepTrace] = Field(default_factory=list)
    chosen_action: Optional[Dict[str, Any]] = None
    chosen_actions: list[Dict[str, Any]] = Field(default_factory=list)
    clarification_turns: int = 0
    should_ask_user: bool = False
    final_device_state: Dict[str, Any] = Field(default_factory=dict)
    ground_truth_state: Dict[str, Any] = Field(default_factory=dict)
    ground_truth_entity: Optional[str] = None
    preferred_action: Optional[Dict[str, Any]] = None
    assertion_results: list[Dict[str, Any]] = Field(default_factory=list)
    action_execution_results: list[Dict[str, Any]] = Field(default_factory=list)
    action_success: Optional[bool] = None
    clarification_success: Optional[bool] = None
    memory_assertion_success: Optional[bool] = None
    final_state_success: Optional[bool] = None
    task_success: Optional[bool] = None
    usage_events: list[Dict[str, Any]] = Field(default_factory=list)
    agent_structured_decisions: list[Dict[str, Any]] = Field(default_factory=list)
    agent_tool_calls: list[Dict[str, Any]] = Field(default_factory=list)
    agent_usage_metadata: list[Dict[str, Any]] = Field(default_factory=list)
    agent_latencies_ms: list[float] = Field(default_factory=list)
    agent_failures: list[str] = Field(default_factory=list)
    agent_model: Optional[str] = None
    agent_provider: Optional[str] = None
    maintenance_events: list[MaintenanceTrace] = Field(default_factory=list)
    memory_status_after: Dict[str, str] = Field(default_factory=dict)
    memory_records_after: list[Dict[str, Any]] = Field(default_factory=list)
    outcome: str = "failure"
    end_to_end_latency_ms: float = 0.0
    maintenance_latency_ms: float = 0.0
    estimated_maintenance_tokens: int = 0
    estimated_prompt_tokens: int = 0
    safety_relevant: bool = False
    safety_gated: bool = False
