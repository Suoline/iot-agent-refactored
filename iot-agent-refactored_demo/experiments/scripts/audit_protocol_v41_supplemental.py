from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit v4.1 supplemental raw-text ingestion traces.")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = json.loads((args.root / "freeze_manifest.json").read_text(encoding="utf-8"))
    config = freeze["config"]
    selected_replicates = freeze.get("executed_replicates", [])
    if not selected_replicates:
        raise SystemExit("freeze manifest has no executed replicate set")
    expected = {(system, trajectory["trajectory_id"], replicate)
                for system in config["systems"] for trajectory in config["trajectories"] for replicate in selected_replicates}
    traces = [json.loads(path.read_text(encoding="utf-8")) for path in sorted((args.root / "units").glob("*/*/*.json"))]
    issues = []
    seen = set()
    for trace in traces:
        key = (trace.get("system_id"), trace.get("trajectory_id"), trace.get("replicate_id"))
        seen.add(key)
        if trace.get("git_revision") != freeze.get("git_revision"):
            issues.append(f"revision_mismatch:{key}")
        if trace.get("agent_backend") != "external_llm":
            issues.append(f"backend:{key}")
        if trace.get("agent_failures"):
            issues.append(f"agent_failure:{key}")
        if trace.get("usage", {}).get("total_tokens", 0) <= 0:
            issues.append(f"usage_missing:{key}")
        if not trace.get("forbidden_runtime_inputs_absent"):
            issues.append(f"forbidden_input:{key}")
        if trace.get("system_id") == "B1" and trace.get("baseline_context_source") != "raw_text_rag":
            issues.append(f"b1_fidelity:{key}")
        if trace.get("system_id") == "B4" and trace.get("baseline_context_source") != "full_raw_history":
            issues.append(f"b4_fidelity:{key}")
        if trace.get("system_id") in {"B0", "B1", "B4"} and trace.get("memory_records_after"):
            issues.append(f"structured_memory_leak:{key}")
        attempts = trace.get("transport_attempts", [])
        # A repair is allowed only after preserving an exhausted original attempt.
        maximum_attempts = (config["requirements"]["max_transport_retries"] + 1) * (2 if trace.get("transport_repair") else 1)
        if len(attempts) > maximum_attempts:
            issues.append(f"retry_budget_exceeded:{key}")
        if trace.get("transport_repair") and not (args.root / "units" / trace["system_id"] / trace["trajectory_id"] / "repair_attempts").exists():
            issues.append(f"repair_provenance_missing:{key}")
    missing = sorted(expected - seen)
    unexpected = sorted(seen - expected)
    if missing:
        issues.append(f"missing_units:{len(missing)}")
    if unexpected:
        issues.append(f"unexpected_units:{len(unexpected)}")
    report = {"protocol": config["protocol"], "selected_replicates": selected_replicates,
              "evidence_grade": "supplemental_preliminary" if len(selected_replicates) >= config["requirements"]["min_complete_replicates_for_supplemental"] else "engineering_pilot",
              "expected_unit_count": len(expected), "observed_unit_count": len(seen),
              "missing_units": missing, "unexpected_units": unexpected, "issues": issues,
              "status": "pass" if not issues else "fail"}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    if issues:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
