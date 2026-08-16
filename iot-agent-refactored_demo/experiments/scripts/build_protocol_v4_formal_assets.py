from __future__ import annotations

"""Build deterministic, reviewable inputs for the protocol-v4 formal run."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SYSTEM_IDS = ["Ours", "B0", "B1", "B2", "B3", "B4", "B5"]
BEHAVIORAL_SCENARIOS = [
    "H2_v4_behavioral", "C2_v4_behavioral", "B6_v4_behavioral",
    "Q1_v4_behavioral", "U1_v4_behavioral", "R1_v4_behavioral",
    "D1_v4_behavioral", "S1_v4_behavioral", "V1_v4_behavioral", "E4_v4_behavioral",
]
LONGITUDINAL_SCENARIOS = ["L1_v4_longitudinal"]
ROBUSTNESS_SCENARIOS = ["B6R_v4_robustness", "B6W_v4_robustness"]
INGESTION_CASES = ["preference_create", "preference_conflict_update"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _revision() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True).strip()


def _scenario_path(scenario_id: str) -> str:
    return f"experiments/scenarios/protocol_v4/{scenario_id.split('_v4_')[0]}.yaml"


def build_matrix(*, workload: str, scenarios: list[str], replicates: list[int], group_id: str) -> dict:
    units = [
        {
            "group_id": group_id,
            "planner_mode": "agent",
            "system_id": system_id,
            "scenario_id": scenario_id,
            "seed": replicate_id,
            "replicate_id": replicate_id,
            "source_planner_mode": "agent",
            "scenario_path": _scenario_path(scenario_id),
            "world_path": "experiments/world_model/v2.json" if scenario_id.endswith("W_v4_robustness") else "experiments/world_model/v1.json",
        }
        for system_id in SYSTEM_IDS
        for scenario_id in scenarios
        for replicate_id in replicates
    ]
    return {
        "matrix_version": "protocol-v4-formal-20260813",
        "evaluation_protocol": "v4",
        "workload": workload,
        "planner_mode": "agent",
        "world_version": "wm-v1",
        "system_policy_version": "sp-v4",
        "scenario_count": len(scenarios),
        "scenario_ids": scenarios,
        "system_ids": SYSTEM_IDS,
        "seed_protocol": "replicate_id",
        "replicate_ids": replicates,
        "unit_count": len(units),
        "requirements": {
            "no_gold_memory_ops": True,
            "no_action_template": True,
            "real_usage_required_for_llm_units": True,
            "transport_failures_excluded_from_metric_denominators": True,
            "resume_supported": True,
            "max_api_calls_before_review": 200,
        },
        "units": units,
    }


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _annotation_task_pack() -> dict:
    return {
        "protocol": "v4",
        "status": "pending_human_annotation",
        "instructions": [
            "两名标注者必须独立完成，不得查看模型输出、系统 trace 或对方答案。",
            "只根据场景原始输入和用户可观察结局填写标签；不评价内部 MemoryRecord 状态。",
            "发生分歧时由第三名裁决者填写 adjudication，且保留原因。",
            "空值不进入 Cohen's kappa 分母，禁止用自动标签、gold evaluator 标签或系统输出补全。",
        ],
        "scenarios": [
            {
                "scenario_id": scenario_id,
                "annotation_file": f"experiments/annotations/protocol_v4/inter_annotator/{scenario_id}.json",
                "required_fields": ["annotator_a", "annotator_b", "adjudication"],
            }
            for scenario_id in [*BEHAVIORAL_SCENARIOS, *LONGITUDINAL_SCENARIOS, *ROBUSTNESS_SCENARIOS]
        ],
        "import_command": "python3 experiments/scripts/compute_annotation_agreement.py --annotation-root experiments/annotations/protocol_v4/inter_annotator --output experiments/annotations/protocol_v4/annotation_agreement.json",
    }


def _freeze_manifest(paths: list[Path]) -> dict:
    contracts = {
        "B0": "stateless; no memory context",
        "B1": "raw-text RAG, top_k=5; no structured MemoryRecord",
        "B2": "structured static memory baseline",
        "B3": "source-prior structured baseline",
        "B4": "all raw conversation/event history; no structured MemoryRecord or retrieval ranking",
        "B5": "GA-inspired heuristic only; not a published Generative Agents or Mem0 implementation",
    }
    return {
        "protocol": "v4",
        "freeze_status": "pre_commit_candidate",
        "code_revision_at_generation": _revision(),
        "model_identity_policy": "Read provider/model from the project configuration at run start; record identities in every trace/manifest; never serialize API credentials.",
        "behavioral_population": BEHAVIORAL_SCENARIOS,
        "worlds": {
            "wm-v1": "experiments/world_model/v1.json",
            "wm-v2-alt-home": "experiments/world_model/v2.json",
        },
        "scope_limit": "Only these v4 no-hidden-bridge behavioral scenarios are eligible for the formal main table. Legacy 36 scenarios are excluded.",
        "baseline_contracts": contracts,
        "primary_metrics": {
            "TSR": "all eligible external action, clarification, final-state, and query assertions pass",
            "Control Final-State TSR": "physical-control scenarios with expect_final_state only",
            "Query Answer Accuracy": "query assertions only",
            "Automation Decision Accuracy": "automation action/no-action assertions only",
            "Wrong-Device Rate": "wrong entity among eligible physical action assertions",
            "Unsafe Action Rate": "unsafe physical execution among evaluator safety-required cases",
            "Clarification Rate": "necessary and unnecessary clarification reported separately",
            "Prompt Tokens": "sum agent_usage_metadata.prompt_tokens from valid external traces",
            "Completion Tokens": "sum agent_usage_metadata.completion_tokens from valid external traces",
            "Latency": "end_to_end_latency_ms from valid external traces",
        },
        "secondary_metrics": {
            "SRR": "evaluator-owned lifecycle truth only",
            "UC": "evaluator-bound old/new correction pairs",
            "Context Efficiency": "TSR divided by real mean prompt tokens; never estimated prompt proxy",
            "Contract Conformance Score": "mechanism diagnostic only; excluded from primary TSR",
        },
        "statistics": {
            "unit": "scenario x replicate_id paired observation",
            "primary": "paired risk difference with two-way clustered bootstrap 95% CI",
            "binary_sensitivity": "paired McNemar or clustered GEE where applicable",
            "continuous": "paired bootstrap CI",
            "multiple_comparison_family": "Ours vs B0-B5 across preregistered primary metrics only; Holm correction",
            "replicate_note": "replicate_id is not a provider-controlled seed when the provider rejects request-level seed",
        },
        "missing_data": {
            "transport_failure": "record and audit separately; exclude from performance denominator; rerun only under declared retry policy",
            "no_agent_call_required": "exclude from LLM usage denominator and report separately",
            "annotation_missing": "exclude from kappa denominator; never impute",
        },
        "files": [
            {
                "path": str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path),
                "sha256": _sha256(path),
            }
            for path in paths
        ],
        "rebuild_command": "PYTHONDONTWRITEBYTECODE=1 python3 experiments/scripts/build_protocol_v4_formal_assets.py",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build frozen protocol-v4 formal inputs without calling an LLM.")
    parser.add_argument("--replicates", default="1001-1030")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "experiments" / "configs")
    args = parser.parse_args()
    start, end = (int(item) for item in args.replicates.split("-", 1))
    replicates = list(range(start, end + 1))
    output_dir = args.output_dir
    formal = output_dir / "protocol_v4_formal_agent_matrix.json"
    longitudinal = output_dir / "protocol_v4_formal_longitudinal_matrix.json"
    robustness = output_dir / "protocol_v4_formal_robustness_matrix.json"
    ingestion = output_dir / "protocol_v4_formal_ingestion_workload.json"
    annotations = REPO_ROOT / "experiments" / "annotations" / "protocol_v4" / "annotation_task_pack.json"
    _write_json(formal, build_matrix(workload="agent_behavioral", scenarios=BEHAVIORAL_SCENARIOS, replicates=replicates, group_id="protocol_v4_formal_agent"))
    _write_json(longitudinal, build_matrix(workload="longitudinal", scenarios=LONGITUDINAL_SCENARIOS, replicates=replicates, group_id="protocol_v4_formal_longitudinal"))
    _write_json(robustness, build_matrix(workload="held_out_paraphrase_noise_second_world", scenarios=ROBUSTNESS_SCENARIOS, replicates=replicates[:2], group_id="protocol_v4_robustness"))
    _write_json(ingestion, {
        "protocol": "v4", "workload": "end_to_end_ingestion", "status": "prepared_not_run",
        "prohibited_runtime_inputs": ["memory_ops", "action_template", "evaluator labels"],
        "cases": INGESTION_CASES,
        "required_evidence": ["raw user text", "SQLite lineage", "retrieval trace", "external action", "real usage metadata"],
    })
    _write_json(annotations, _annotation_task_pack())
    manifest_inputs = [
        formal, longitudinal, robustness, ingestion,
        REPO_ROOT / "experiments/configs/protocol_v4_split.json",
        REPO_ROOT / "experiments/runner/system_registry.py",
        REPO_ROOT / "experiments/runner/single_run.py",
        REPO_ROOT / "experiments/planners/agent_planner.py",
        REPO_ROOT / "experiments/metrics/core.py",
        REPO_ROOT / "experiments/evaluator/ground_truth.py",
        REPO_ROOT / "experiments/evaluator/lifecycle.py",
        REPO_ROOT / "experiments/evaluator/protocol.py",
        REPO_ROOT / "experiments/scripts/analyze_protocol_v4_formal.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_freeze.py",
        REPO_ROOT / "experiments/scripts/seal_protocol_v4_freeze.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_ingestion.py",
        REPO_ROOT / "experiments/scripts/audit_llm_assisted_annotation.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_longitudinal.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_preflight.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_readiness.py",
        REPO_ROOT / "experiments/scripts/audit_protocol_v4_traces.py",
        REPO_ROOT / "experiments/scripts/compute_annotation_agreement.py",
        REPO_ROOT / "experiments/annotations/protocol_v4/model_independent/annotator_a.json",
        REPO_ROOT / "experiments/annotations/protocol_v4/model_independent/annotator_b.json",
        REPO_ROOT / "experiments/annotations/protocol_v4/model_independent/adjudicator_c.json",
        REPO_ROOT / "experiments/annotations/protocol_v4/llm_assisted_annotation_report.json",
        REPO_ROOT / "experiments/scripts/run_strict_serial_unit.py",
        REPO_ROOT / "experiments/scripts/run_strict_group.py",
        REPO_ROOT / "experiments/scripts/probe_external_llm_seed_support.py",
        REPO_ROOT / "experiments/memory/text_ingestion.py",
        REPO_ROOT / "experiments/world_model/ha_oracle.py",
        REPO_ROOT / "experiments/world_model/v1.json",
        REPO_ROOT / "experiments/world_model/v2.json",
        *[REPO_ROOT / _scenario_path(item) for item in [*BEHAVIORAL_SCENARIOS, *LONGITUDINAL_SCENARIOS, *ROBUSTNESS_SCENARIOS]],
    ]
    _write_json(output_dir / "protocol_v4_formal_freeze_manifest.json", _freeze_manifest(manifest_inputs))
    print(output_dir / "protocol_v4_formal_freeze_manifest.json")


if __name__ == "__main__":
    main()
