from __future__ import annotations

import json
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.ablations import ABLATION_IDS
from experiments.baselines import BASELINE_IDS
from experiments.scripts._artifact_paths import configured_run_id, reports_root, result_stage, results_root


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _check(condition: bool, code: str, details, failures: list[dict]) -> None:
    if not condition:
        failures.append({"code": code, "details": details})


def main() -> None:
    failures: list[dict] = []
    warnings: list[dict] = []
    expected = [
        (configured_run_id("oracle"), "Ours", "oracle"),
        (configured_run_id("agent"), "Ours", "agent"),
        *[(configured_run_id("baseline"), system_id, "oracle") for system_id in BASELINE_IDS],
        *[(configured_run_id("ablation"), system_id, "oracle") for system_id in ABLATION_IDS],
    ]
    manifests = []
    for run_id, system_id, planner_mode in expected:
        path = results_root() / "reports" / run_id / system_id / planner_mode / "manifest.json"
        _check(path.exists(), "missing_manifest", str(path), failures)
        if not path.exists():
            continue
        manifest = _load(path)
        manifests.append(manifest)
        if result_stage().startswith("formal"):
            required_seed_count = 20 if run_id in {configured_run_id("agent"), configured_run_id("ablation")} else 30
            _check(
                manifest.get("seed_count") == required_seed_count,
                "formal_seed_count_mismatch",
                {"manifest": str(path), "expected": required_seed_count, "observed": manifest.get("seed_count")},
                failures,
            )
        trace_files = manifest.get("trace_files", [])
        _check(len(trace_files) == manifest.get("task_count"), "trace_count_mismatch", str(path), failures)
        trace_failures = []
        for relative in trace_files:
            trace_path = results_root() / relative
            _check(trace_path.exists(), "missing_trace", relative, failures)
            if not trace_path.exists():
                continue
            trace = _load(trace_path)
            coherent = (trace.get("outcome") == "success") == bool(trace.get("task_success"))
            _check(coherent, "outcome_task_success_mismatch", relative, failures)
            if trace.get("final_state_success") is not None:
                final_assertions = [
                    item for item in trace.get("assertion_results", []) if item.get("kind") == "final_state"
                ]
                _check(bool(final_assertions), "missing_final_state_assertion", relative, failures)
                _check(
                    all(item.get("success") for item in final_assertions) == trace.get("final_state_success"),
                    "final_state_flag_mismatch",
                    relative,
                    failures,
                )
            if trace.get("outcome") != "success":
                trace_failures.append(trace.get("task_id"))
        _check(
            sorted(trace_failures) == sorted(manifest.get("failed_task_ids", [])),
            "manifest_failed_tasks_mismatch",
            str(path),
            failures,
        )
        if planner_mode == "oracle":
            _check(manifest.get("sampling_unit") == "scenario", "oracle_sampling_unit", str(path), failures)
        if planner_mode == "agent" and manifest.get("result_classification") != "confirmatory":
            warnings.append(
                {
                    "code": "agent_not_confirmatory",
                    "details": {
                        "classification": manifest.get("result_classification"),
                        "backends": manifest.get("agent_backends", []),
                    },
                }
            )

    for filename in ["table_1.csv", "table_2.csv", "table_3.csv", "table_4.csv", "table_5.csv"]:
        path = results_root() / "tables" / result_stage() / filename
        _check(path.exists() and path.stat().st_size > 20, "empty_or_missing_table", str(path), failures)

    root = results_root() / "aggregated_metrics"
    b0_path = root / configured_run_id("baseline") / "B0" / "oracle" / "metrics.json"
    b4_path = root / configured_run_id("baseline") / "B4" / "oracle" / "metrics.json"
    if b0_path.exists() and b4_path.exists():
        b0, b4 = _load(b0_path), _load(b4_path)
        _check(b0 != b4, "b0_b4_equivalent", None, failures)
        _check(
            b4.get("Estimated Prompt Tokens", 0) > b0.get("Estimated Prompt Tokens", 0),
            "b4_context_not_larger",
            {"B0": b0.get("Estimated Prompt Tokens"), "B4": b4.get("Estimated Prompt Tokens")},
            failures,
        )

    significance_path = reports_root() / "significance_summary.json"
    if significance_path.exists():
        for row in _load(significance_path):
            if row.get("planner_mode") == "oracle":
                _check(row.get("sampling_unit") == "scenario", "significance_sampling_unit", row, failures)

    annotation_path = REPO_ROOT / "experiments" / "annotations" / "annotation_agreement.json"
    annotation = _load(annotation_path) if annotation_path.exists() else {"status": "missing"}
    if annotation.get("status") != "complete":
        warnings.append({"code": "human_annotation_pending", "details": annotation})

    report = {
        "stage": result_stage(),
        "status": "pass" if not failures else "fail",
        "confirmatory_scope": "oracle_only" if not failures else "none",
        "agent_scope": "exploratory_or_fallback",
        "manifest_count": len(manifests),
        "failures": failures,
        "warnings": warnings,
    }
    out = reports_root() / "artifact_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
