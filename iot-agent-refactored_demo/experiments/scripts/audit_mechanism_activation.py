from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any


ABLATION_CONTRACTS = {
    "-Decay": {"mechanism": "dynamic_confidence", "allowed_config_differences": {"use_dynamic_confidence"}},
    "-AsymFeedback": {"mechanism": "asym_feedback", "allowed_config_differences": {"alpha_neg"}},
    "-Governance": {
        "mechanism": "governance",
        "allowed_config_differences": {"use_governance", "use_resampling", "use_content_aging"},
    },
    "-CandidateGate": {"mechanism": "candidate_gate", "allowed_config_differences": {"use_candidate_gate"}},
    "-ConflictHandling": {"mechanism": "conflict_handling", "allowed_config_differences": {"use_conflict_handling"}},
    "-FeatureAbsorption": {"mechanism": "feature_absorption", "allowed_config_differences": {"use_feature_absorption"}},
    "-Ripple": {"mechanism": "ripple", "allowed_config_differences": {"use_ripple"}},
    "-Split": {"mechanism": "split", "allowed_config_differences": {"use_split"}},
}


def _events(trace: dict[str, Any]) -> set[str]:
    kinds = set()
    for event in trace.get("usage_events", []):
        kinds.add(str(event.get("kind", "")))
    for maintenance in trace.get("maintenance_events", []):
        for key in ("expired_memory_ids", "stale_memory_ids", "resampled_memory_ids", "rollback_merge_ids", "needs_review_ids"):
            if maintenance.get(key):
                kinds.add(key)
    return kinds


def audit_activation(trace: dict[str, Any], system_id: str) -> dict[str, Any]:
    """Require an ablated mechanism to be invoked while its target flag is disabled."""
    contract = ABLATION_CONTRACTS.get(system_id)
    if contract is None:
        return {"status": "not_an_ablation", "system_id": system_id}
    target = contract["mechanism"]
    rows = [row for row in trace.get("mechanism_activation", []) if row.get("mechanism") == target]
    disabled_when_invoked = bool(rows) and all(row.get("enabled") is False for row in rows)
    config = trace.get("system_configuration", {})
    from experiments.runner.system_registry import build_system_registry

    ours = build_system_registry()["Ours"].__dict__
    ignored = {"system_id", "notes", "planner_mode", "evaluation_protocol"}
    differences = {
        key for key, value in config.items()
        if key not in ignored and key in ours and value != ours[key]
    }
    unexpected_differences = sorted(differences - contract["allowed_config_differences"])
    isolation_ok = bool(config) and not unexpected_differences
    passed = disabled_when_invoked and isolation_ok
    return {
        "system_id": system_id,
        "target_mechanism": target,
        "activation_rows": rows,
        "target_invoked": bool(rows),
        "disabled_when_invoked": disabled_when_invoked,
        "isolation_ok": isolation_ok,
        "unexpected_config_differences": unexpected_differences,
        "status": "pass" if passed else "inconclusive",
        "warning": None if passed else "目标机制未被调用、未被禁用，或消融包含未声明的额外配置差异",
    }


def audit_files(paths: list[Path], system_id: str) -> dict[str, Any]:
    rows = []
    for path in paths:
        rows.append(audit_activation(json.loads(path.read_text(encoding="utf-8")), system_id))
    return {
        "system_id": system_id,
        "trace_count": len(rows),
        "status": "pass" if rows and all(row["status"] == "pass" for row in rows) else "inconclusive",
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--system-id", required=True)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    report = audit_files(args.paths, args.system_id)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
