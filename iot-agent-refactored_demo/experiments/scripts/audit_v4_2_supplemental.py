from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _files(root: Path):
    return sorted(path for path in root.rglob("*") if path.is_file())


def _audit_rows(rows, expected, revision):
    issues = []
    seen = set()
    for row in rows:
        key = (row.get("system_id"), row.get("trajectory_id"), row.get("replicate_id"))
        seen.add(key)
        if row.get("git_revision") != revision:
            issues.append(f"revision_mismatch:{key}")
        if row.get("agent_backend") not in {"external_llm", "product_runtime"}:
            issues.append(f"backend:{key}")
        if not row.get("usage", {}).get("total_tokens", 0) and row.get("agent_backend") == "external_llm":
            issues.append(f"usage_missing:{key}")
        if row.get("fallback_used"):
            issues.append(f"fallback:{key}")
        if row.get("forbidden_runtime_inputs_present"):
            issues.append(f"forbidden_input:{key}")
        if row.get("experiment_group") == "product_runtime" and not row.get("task_success"):
            issues.append(f"runtime_failure:{key}")
        if row.get("experiment_group") == "mechanism":
            applied = row.get("applied_operations", [])
            if not applied or any(item.get("status") != "applied" for item in applied):
                issues.append(f"operation_not_applied:{key}")
            if not row.get("target_activation"):
                issues.append(f"target_activation_missing:{key}")
        if row.get("experiment_group") == "longitudinal":
            system_id = row.get("system_id")
            source = row.get("baseline_context_source")
            if system_id == "B1" and source != "raw_text_rag":
                issues.append(f"b1_fidelity:{key}")
            if system_id == "B4" and source != "full_raw_history":
                issues.append(f"b4_fidelity:{key}")
    missing = sorted(expected - seen)
    if missing:
        issues.append(f"missing_units:{len(missing)}")
    return {"expected_unit_count": len(expected), "observed_unit_count": len(seen), "missing_units": missing, "issues": issues, "status": "pass" if not issues else "fail"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--group", choices=["mechanism", "longitudinal", "product_runtime"], action="append")
    args = parser.parse_args()
    freeze = json.loads((args.root / "freeze_manifest.json").read_text(encoding="utf-8"))
    revision = freeze["git_revision"]
    rows = [json.loads(path.read_text(encoding="utf-8")) for path in _files(args.root / "units") if path.suffix == ".json"]
    replicates = freeze["executed_replicates"]
    config = freeze["config"]
    groups = {}
    for name, systems, trajectories in (("mechanism", config["systems"], config["mechanism_trajectories"]), ("longitudinal", config["longitudinal_systems"], config["longitudinal_trajectories"])):
        if name == "mechanism":
            expected = {
                (system, item["trajectory_id"], replicate)
                for item in trajectories for system in ("Ours", item["system_id"]) for replicate in replicates
            }
        else:
            expected = {(system, item["trajectory_id"], replicate) for system in systems for item in trajectories for replicate in replicates}
        groups[name] = _audit_rows([row for row in rows if row.get("experiment_group") == name], expected, revision)
    runtime_rows = [row for row in rows if row.get("experiment_group") == "product_runtime"]
    runtime_expected = {(item["trajectory_id"], replicate) for item in config["product_runtime_trajectories"] for replicate in replicates}
    groups["product_runtime"] = _audit_rows(runtime_rows, {(None, key[0], key[1]) for key in runtime_expected}, revision)
    selected_groups = args.group or list(groups)
    report = {"protocol": config["protocol"], "git_revision": revision, "selected_replicates": replicates, "groups": groups, "selected_groups": selected_groups, "status": "pass" if all(groups[name]["status"] == "pass" for name in selected_groups) else "fail", "human_annotation_complete": False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if report["status"] != "pass":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
