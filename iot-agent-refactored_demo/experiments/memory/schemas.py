from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel, Field


MemoryStatus = Literal[
    "candidate",
    "active",
    "stale",
    "conflicted",
    "superseded",
    "expired",
    "archived",
    "deleted",
]
MemoryLayer = Literal["active", "dormant", "archived"]
MemorySource = Literal[
    "ha_registry",
    "ha_state_observation",
    "user_explicit",
    "user_correction",
    "user_behavior",
    "execution_verification",
    "llm_inference",
    "imported_doc",
]


class MemoryRecord(BaseModel):
    memory_id: str
    scope: str
    device_id: Optional[str] = None
    entity_id: Optional[str] = None
    room_id: Optional[str] = None
    user_id: Optional[str] = None
    memory_type: str
    subject: str
    predicate: str
    object: str
    condition: Optional[str] = None
    action: Optional[str] = None
    natural_text: str
    structured_payload: Dict[str, Any] = Field(default_factory=dict)
    source: MemorySource
    evidence_refs: list[Dict[str, Any]] = Field(default_factory=list)
    source_turn_id: Optional[str] = None
    source_trace_id: Optional[str] = None
    confidence: float
    source_authority: float
    importance: float = 0.5
    volatility: str = "medium"
    positive_hits: int = 0
    negative_hits: int = 0
    ripple_penalty: float = 0.0
    created_at: datetime
    updated_at: datetime
    last_accessed_at: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    half_life_days: int
    status: MemoryStatus = "candidate"
    layer: MemoryLayer = "active"
    supersedes: list[str] = Field(default_factory=list)
    superseded_by: Optional[str] = None
    conflicts_with: list[str] = Field(default_factory=list)
    merged_from: list[str] = Field(default_factory=list)
    coverage_proof: Optional[Dict[str, Any]] = None
    related_memory_ids: list[str] = Field(default_factory=list)
    depends_on_memory_ids: list[str] = Field(default_factory=list)
    derived_from_memory_ids: list[str] = Field(default_factory=list)
    sensitive: bool = False
    needs_review: bool = False
    resampled: bool = False
    access_count: int = 0
    update_count: int = 0
    last_used_task_id: Optional[str] = None


class MemoryEdge(BaseModel):
    edge_id: str
    source_id: str
    relation: str
    target_id: str
    confidence: float
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    source_memory_id: str


class UsageEvent(BaseModel):
    task_id: str
    memory_id: str
    used_stage: Literal[
        "device_filter",
        "constraint_filter",
        "planning",
        "execution",
        "verification",
    ]
    contribution: Literal["helpful", "neutral", "misleading", "unknown"]
    outcome: Literal["success", "partial_success", "failure"]
    verification_delta: Optional[float] = None
    note: str = ""
    timestamp: datetime


class MatchedMemory(BaseModel):
    memory_id: str
    memory_type: str
    text: str
    score: float
    raw_confidence: float
    effective_confidence: float
    memory_worth: float
    system_status: str
    true_status: str
    runtime_status: str
    layer: str
    in_usable_set: bool = False
    in_grounding_set: bool = False


class CandidateDevice(BaseModel):
    entity_id: str
    name: str
    score: float
    confidence: float
    matched_memories: list[MatchedMemory] = Field(default_factory=list)
    missing_info: list[str] = Field(default_factory=list)


class SearchResultPackage(BaseModel):
    query: str
    candidate_devices: list[CandidateDevice] = Field(default_factory=list)
    global_constraints: list[MatchedMemory] = Field(default_factory=list)
    matched_memories: list[MatchedMemory] = Field(default_factory=list)
    should_ask_user: bool = False
    ask_reason: Optional[str] = None
    threshold_used: float = 0.70
    task_type: str = "control"
    retrieval_metadata: Dict[str, Any] = Field(default_factory=dict)
