from __future__ import annotations

import argparse
import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(path: Path, default: dict | None = None) -> dict:
    if not path.exists():
        return default or {}
    return json.loads(path.read_text(encoding="utf-8"))


def audit(
    *,
    pilot_root: Path,
    pilot_run_id: str,
    annotation_report: Path | None = None,
    preflight_audit: Path | None = None,
    freeze_audit: Path | None = None,
    allow_llm_assisted_annotation: bool = False,
) -> dict:
    matrix = _load(REPO_ROOT / "experiments" / "configs" / "protocol_v4_pilot_matrix.json")
    activation = _load(
        REPO_ROOT
        / "experiments"
        / "results"
        / "protocol_v4_activation_dry_run"
        / "reports"
        / "activation_contract_20260810"
        / "mechanism_activation_audit.json"
    )
    annotation_path = annotation_report or (
        REPO_ROOT / "experiments" / "annotations" / "protocol_v4" / "annotation_agreement.json"
    )
    annotation = _load(annotation_path)
    preflight = _load(preflight_audit) if preflight_audit else {}
    freeze = _load(freeze_audit) if freeze_audit else {}
    llm_assisted = (
        annotation.get("status") == "complete_llm_assisted"
        and annotation.get("researcher_accepted_for_execution") is True
        and annotation.get("scenario_count") == 13
        and annotation.get("model_model_cohen_kappa") is not None
        and not annotation.get("issues")
    )
    formal_manifest = _load(
        REPO_ROOT / "experiments" / "configs" / "protocol_v4_formal_freeze_manifest.json"
    )
    formal_matrix = _load(
        REPO_ROOT / "experiments" / "configs" / "protocol_v4_formal_agent_matrix.json"
    )
    longitudinal_matrix = _load(
        REPO_ROOT / "experiments" / "configs" / "protocol_v4_formal_longitudinal_matrix.json"
    )
    robustness_matrix = _load(
        REPO_ROOT / "experiments" / "configs" / "protocol_v4_formal_robustness_matrix.json"
    )
    pilot_audit = _load(
        pilot_root / "reports" / pilot_run_id / "protocol_v4_agent_pilot.strict_audit.json"
    )
    traces = sorted((pilot_root / "raw_traces" / pilot_run_id).rglob("*.json"))
    task_traces = [path for path in traces if not path.name.endswith(".maintenance.json")]
    probes = sorted((pilot_root / "reports").glob("*/external_llm_seed_probe.json"))
    valid_external = []
    transport_failures = []
    for path in task_traces:
        trace = _load(path)
        if trace.get("agent_backend") == "external_llm" and not trace.get("agent_failures"):
            valid_external.append(str(path.relative_to(pilot_root)))
        if any(str(item).startswith(("external_call_failed:", "external_init_failed:")) for item in trace.get("agent_failures", [])):
            transport_failures.append(str(path.relative_to(pilot_root)))
    probe_failures = []
    probe_successes = []
    for path in probes:
        probe = _load(path)
        if probe.get("probe_status") in {"ok", "success"}:
            probe_successes.append(str(path.relative_to(pilot_root)))
        elif probe.get("probe_status") == "error":
            probe_failures.append(
                {
                    "path": str(path.relative_to(pilot_root)),
                    "failure_type": probe.get("failure_type"),
                    "failure_message": probe.get("failure_message"),
                }
            )

    checks = {
        "pilot_matrix_frozen": matrix.get("evaluation_protocol") == "v4" and matrix.get("unit_count") == 42,
        "activation_contract_passed": activation.get("status") == "pass" and activation.get("row_count") == 8,
        "annotation_report_present": bool(annotation),
        "annotation_complete": (
            annotation.get("status") == "complete"
            and annotation.get("pending_count") == 0
            and annotation.get("adjudication_required_count") == 0
            and annotation.get("cohen_kappa") is not None
        ) or (allow_llm_assisted_annotation and llm_assisted),
        "llm_assisted_annotation_used": bool(allow_llm_assisted_annotation and llm_assisted),
        "external_pilot_has_valid_trace": bool(valid_external),
        "external_pilot_transport_clean": not transport_failures,
        "formal_manifest_present": formal_manifest.get("protocol") == "v4",
        "formal_main_matrix_complete": formal_matrix.get("unit_count") == 7 * 10 * 30,
        "formal_longitudinal_matrix_complete": longitudinal_matrix.get("unit_count") == 7 * 1 * 30,
        "held_out_robustness_matrix_present": robustness_matrix.get("unit_count") == 7 * 2 * 2,
        "preflight_engineering_ready": preflight.get("status") == "engineering_ready_for_formal_run",
        "post_commit_freeze_passed": freeze.get("status") == "pass",
    }
    formal_run_blockers = []
    if not checks["annotation_complete"]:
        formal_run_blockers.append("complete_real_human_annotation_and_adjudication")
    if not checks["preflight_engineering_ready"]:
        formal_run_blockers.append("pass_70_unit_preflight")
    if not checks["post_commit_freeze_passed"]:
        formal_run_blockers.append("pass_post_commit_freeze_audit")
    if transport_failures:
        status = "blocked_on_llm_authorization"
    elif not formal_run_blockers:
        status = "engineering_ready_for_formal_run"
    elif checks["preflight_engineering_ready"] and checks["post_commit_freeze_passed"]:
        status = "engineering_ready_but_annotation_blocked"
    elif checks["preflight_engineering_ready"]:
        status = "engineering_ready_but_freeze_blocked"
    elif checks["external_pilot_has_valid_trace"] and checks["external_pilot_transport_clean"]:
        status = "pilot_evidence_available_not_formal_ready"
    else:
        status = "local_gates_passed_external_pilot_not_started"
    return {
        "protocol": "v4",
        "status": status,
        "checks": checks,
        "pilot_matrix_unit_count": matrix.get("unit_count"),
        "activation_audit": activation.get("status"),
        "annotation_status": annotation.get("status"),
        "annotation_pending_count": annotation.get("pending_count"),
        "annotation_adjudication_required_count": annotation.get("adjudication_required_count"),
        "cohen_kappa": annotation.get("cohen_kappa"),
        "model_model_cohen_kappa": annotation.get("model_model_cohen_kappa"),
        "annotation_protocol_deviation": annotation.get("protocol_deviation"),
        "preflight_status": preflight.get("status"),
        "freeze_audit_status": freeze.get("status"),
        "formal_freeze_status": formal_manifest.get("freeze_status"),
        "formal_matrix_unit_count": formal_matrix.get("unit_count"),
        "formal_longitudinal_unit_count": longitudinal_matrix.get("unit_count"),
        "robustness_unit_count": robustness_matrix.get("unit_count"),
        "pilot_audit_status": pilot_audit.get("status"),
        "valid_external_trace_count": len(valid_external),
        "valid_external_traces": valid_external,
        "transport_failure_trace_count": len(transport_failures),
        "transport_failure_traces": transport_failures,
        "seed_probe_count": len(probes),
        "seed_probe_success_count": len(probe_successes),
        "seed_probe_successes": probe_successes,
        "seed_probe_failure_count": len(probe_failures),
        "seed_probe_failures": probe_failures,
        "formal_run_blockers": formal_run_blockers,
        "formal_matrix_has_not_run": True,
        "external_validity_limitations": ["second_model_not_run", "second_world_only_pilot_or_pending"],
        "annotation_claim_limit": annotation.get("claim_limit") if checks["llm_assisted_annotation_used"] else None,
        "next_condition": {
            "blocked_on_llm_authorization": "Provide valid project LLM authorization before any matrix expansion.",
            "engineering_ready_for_formal_run": "All formal-start gates passed; the frozen behavioral matrix may start.",
            "engineering_ready_but_annotation_blocked": "Complete independent human annotation and third-party adjudication before starting the formal matrix.",
            "engineering_ready_but_freeze_blocked": "Generate and pass a post-commit freeze audit before starting the formal matrix.",
        }.get(status, "Complete the reported formal_run_blockers before starting the formal matrix."),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pilot-root",
        type=Path,
        default=REPO_ROOT / "experiments" / "results" / "protocol_v4_external_pilot",
    )
    parser.add_argument("--pilot-run-id", default="protocol_v4_external_pilot_20260810")
    parser.add_argument("--annotation-report", type=Path, default=None)
    parser.add_argument("--preflight-audit", type=Path, default=None)
    parser.add_argument("--freeze-audit", type=Path, default=None)
    parser.add_argument("--allow-llm-assisted-annotation", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = audit(
        pilot_root=args.pilot_root,
        pilot_run_id=args.pilot_run_id,
        annotation_report=args.annotation_report,
        preflight_audit=args.preflight_audit,
        freeze_audit=args.freeze_audit,
        allow_llm_assisted_annotation=args.allow_llm_assisted_annotation,
    )
    output = args.output or args.pilot_root / "reports" / args.pilot_run_id / "protocol_v4_readiness_audit.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
