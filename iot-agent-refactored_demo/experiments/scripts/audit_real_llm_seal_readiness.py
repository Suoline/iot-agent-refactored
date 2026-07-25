from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
CANDIDATE_ROOT = (
    REPO_ROOT
    / "experiments"
    / "results"
    / "agent_llm_smoke"
    / "reports"
    / "real_llm_candidate_20260725_two_seed"
    / "Ours"
    / "agent"
)
FORMAL_AUDIT_PATH = (
    REPO_ROOT
    / "experiments"
    / "results"
    / "formal_v2"
    / "reports"
    / "formal_v2"
    / "artifact_audit.json"
)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _check(condition: bool, code: str, evidence, failures: list[dict], passes: list[dict]) -> None:
    item = {"code": code, "evidence": evidence}
    if condition:
        passes.append(item)
    else:
        failures.append(item)


def main() -> None:
    manifest = _load(CANDIDATE_ROOT / "manifest.json")
    audit = _load(CANDIDATE_ROOT / "audit.json")
    comparison = _load(CANDIDATE_ROOT / "comparison.json")
    formal_audit = _load(FORMAL_AUDIT_PATH)

    passes: list[dict] = []
    failures: list[dict] = []

    _check(
        manifest.get("agent_backends") == ["external_llm"],
        "all_backends_external_llm",
        manifest.get("agent_backends"),
        failures,
        passes,
    )
    _check(
        audit.get("no_heuristic_fallback") is True,
        "no_heuristic_fallback",
        audit.get("no_heuristic_fallback"),
        failures,
        passes,
    )
    _check(
        audit.get("task_success_count") == manifest.get("task_count"),
        "all_selected_traces_task_success",
        {
            "task_success_count": audit.get("task_success_count"),
            "task_count": manifest.get("task_count"),
        },
        failures,
        passes,
    )
    _check(
        manifest.get("scenario_count") == 5,
        "agent_scenario_count_matches_current_suite",
        manifest.get("scenario_count"),
        failures,
        passes,
    )
    _check(
        manifest.get("seed_count") >= 2,
        "two_seed_candidate_available",
        manifest.get("seed_count"),
        failures,
        passes,
    )
    _check(
        manifest.get("seed_count") >= 20,
        "secondary_seed_target_reached",
        {
            "observed": manifest.get("seed_count"),
            "required": 20,
        },
        failures,
        passes,
    )
    _check(
        len(set((manifest.get("source_run_git_revisions") or {}).values())) == 1,
        "single_source_git_revision",
        manifest.get("source_run_git_revisions"),
        failures,
        passes,
    )
    _check(
        formal_audit.get("status") == "pass" and formal_audit.get("confirmatory_scope") == "oracle_only",
        "formal_v2_oracle_confirmatory_available",
        {
            "status": formal_audit.get("status"),
            "confirmatory_scope": formal_audit.get("confirmatory_scope"),
        },
        failures,
        passes,
    )
    _check(
        manifest.get("result_classification") == "confirmatory" and manifest.get("confirmatory_ready") is True,
        "real_llm_agent_confirmatory_ready",
        {
            "result_classification": manifest.get("result_classification"),
            "confirmatory_ready": manifest.get("confirmatory_ready"),
        },
        failures,
        passes,
    )
    _check(
        audit.get("failed_attempts") == [],
        "no_recorded_failed_attempts",
        audit.get("failed_attempts"),
        failures,
        passes,
    )

    report = {
        "run_id": manifest.get("run_id"),
        "status": "candidate_only" if failures else "confirmatory_ready",
        "summary": {
            "seed_count": manifest.get("seed_count"),
            "scenario_count": manifest.get("scenario_count"),
            "task_count": manifest.get("task_count"),
            "agent_api_call_count": manifest.get("agent_api_call_count"),
            "agent_usage_totals": audit.get("agent_usage_totals"),
            "candidate_means": comparison.get("candidate_means"),
        },
        "passes": passes,
        "failures": failures,
        "blocking_conclusion": [
            "当前真实 LLM Agent 证据链已满足 external_llm / 无 fallback / 10/10 success / trace-consistent 的候选结果要求。",
            "当前仍未满足论文最终封版要求：secondary seed 数不足 20，来源 run 不是单一 clean revision，且保留了 B6@1002 的失败首次尝试。",
        ],
    }
    out_path = CANDIDATE_ROOT / "seal_readiness_audit.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out_path)


if __name__ == "__main__":
    main()
