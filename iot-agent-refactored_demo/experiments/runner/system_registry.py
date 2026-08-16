from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SystemConfig:
    system_id: str
    use_memory: bool = True
    use_structure: bool = True
    use_lifecycle: bool = True
    use_dynamic_confidence: bool = True
    use_governance: bool = True
    use_candidate_gate: bool = True
    use_conflict_handling: bool = True
    use_feature_absorption: bool = True
    use_ripple: bool = True
    use_split: bool = True
    use_resampling: bool = True
    use_content_aging: bool = True
    alpha_pos: float = 0.04
    alpha_neg: float = 0.20
    score_mode: str = "ours"
    top_k: int = 10
    active_target_limit: int = 500
    dormant_target_limit: int = 300
    archived_target_limit: int = 200
    planner_mode: str = "oracle"
    evaluation_protocol: str = "legacy_v3"
    notes: list[str] = field(default_factory=list)


def build_system_registry() -> dict[str, SystemConfig]:
    return {
        "Ours": SystemConfig(system_id="Ours"),
        "B0": SystemConfig(
            system_id="B0",
            use_memory=False,
            use_structure=False,
            use_lifecycle=False,
            use_dynamic_confidence=False,
            use_governance=False,
            use_candidate_gate=False,
            use_conflict_handling=False,
            use_feature_absorption=False,
            use_ripple=False,
            use_split=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="stateless",
        ),
        "B1": SystemConfig(
            system_id="B1",
            use_structure=False,
            use_lifecycle=False,
            use_dynamic_confidence=False,
            use_governance=False,
            use_candidate_gate=False,
            use_conflict_handling=False,
            use_feature_absorption=False,
            use_ripple=False,
            use_split=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="rag_only",
            notes=["v4: raw-text RAG-only; does not use structured MemoryRecord"],
        ),
        "B2": SystemConfig(
            system_id="B2",
            use_lifecycle=False,
            use_dynamic_confidence=False,
            use_governance=False,
            use_candidate_gate=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="structured_static",
        ),
        "B3": SystemConfig(
            system_id="B3",
            use_dynamic_confidence=False,
            use_governance=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="source_prior",
        ),
        "B4": SystemConfig(
            system_id="B4",
            use_memory=False,
            use_structure=False,
            use_lifecycle=False,
            use_dynamic_confidence=False,
            use_governance=False,
            use_candidate_gate=False,
            use_conflict_handling=False,
            use_feature_absorption=False,
            use_ripple=False,
            use_split=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="large_context",
            top_k=10000,
            notes=["v4: full raw conversation/event history; no structured MemoryRecord or retrieval ranking"],
        ),
        "B5": SystemConfig(
            system_id="B5",
            use_lifecycle=False,
            use_dynamic_confidence=False,
            use_governance=False,
            use_candidate_gate=False,
            use_resampling=False,
            use_content_aging=False,
            score_mode="ga_inspired_heuristic",
            notes=["GA-inspired heuristic, not an implementation of Generative Agents or Mem0"],
        ),
        "-Decay": SystemConfig(system_id="-Decay", use_lifecycle=True, use_dynamic_confidence=False),
        "-AsymFeedback": SystemConfig(system_id="-AsymFeedback", alpha_pos=0.04, alpha_neg=0.04),
        "-Governance": SystemConfig(system_id="-Governance", use_governance=False, use_resampling=False, use_content_aging=False),
        "-CandidateGate": SystemConfig(system_id="-CandidateGate", use_candidate_gate=False),
        "-ConflictHandling": SystemConfig(system_id="-ConflictHandling", use_conflict_handling=False),
        "-FeatureAbsorption": SystemConfig(system_id="-FeatureAbsorption", use_feature_absorption=False),
        "-Ripple": SystemConfig(system_id="-Ripple", use_ripple=False),
        "-Split": SystemConfig(system_id="-Split", use_split=False),
    }
