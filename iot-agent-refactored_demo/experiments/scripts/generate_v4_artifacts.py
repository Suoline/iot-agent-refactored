from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _run(args: list[str]) -> None:
    subprocess.run([sys.executable, *args], cwd=REPO_ROOT, check=True)


def _included_assets() -> list[str]:
    assets = [
        "experiments/scenarios/protocol_v4/H2.yaml",
        "experiments/scenarios/protocol_v4/C2.yaml",
        "experiments/scenarios/protocol_v4/B6.yaml",
        "experiments/scenarios/protocol_v4/B6R.yaml",
        "experiments/scenarios/protocol_v4/L1.yaml",
        "experiments/world_model/v1.json",
        "experiments/runner/system_registry.py",
        "experiments/runner/single_run.py",
        "experiments/planners/agent_planner.py",
        "experiments/evaluator/lifecycle.py",
        "experiments/evaluator/protocol.py",
        "experiments/evaluator/ground_truth.py",
        "experiments/baselines/raw_text.py",
        "experiments/memory/text_ingestion.py",
        "experiments/metrics/core.py",
        "experiments/configs/protocol_v4_split.json",
        "experiments/configs/protocol_v4_longitudinal_matrix.json",
        "experiments/configs/protocol_v4_formal_agent_matrix.json",
        "experiments/configs/protocol_v4_formal_longitudinal_matrix.json",
        "experiments/configs/protocol_v4_formal_robustness_matrix.json",
        "experiments/configs/protocol_v4_formal_ingestion_workload.json",
        "experiments/configs/protocol_v4_formal_freeze_manifest.json",
        "experiments/annotations/protocol_v4/evaluator_ground_truth.json",
        "experiments/annotations/protocol_v4/annotation_agreement.json",
        "experiments/annotations/protocol_v4/annotation_task_pack.json",
    ]
    return [path for path in assets if (REPO_ROOT / path).exists()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild the project-local v4 protocol artifact manifest.")
    parser.add_argument(
        "--artifact-output",
        type=Path,
        default=REPO_ROOT / "experiments" / "results" / "protocol_v4_artifact_bundle",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        default=REPO_ROOT / "experiments" / "results",
        help="Project-local result root whose audit/statistics artifacts should be hashed when present.",
    )
    args = parser.parse_args()
    args.artifact_output.mkdir(parents=True, exist_ok=True)
    _run(["experiments/scripts/build_protocol_v4_pilot_matrix.py"])
    _run([
        "experiments/scripts/build_protocol_v4_pilot_matrix.py",
        "--workload", "longitudinal",
        "--output", "experiments/configs/protocol_v4_longitudinal_matrix.json",
    ])
    _run(["experiments/scripts/sync_ground_truth.py", "--protocol", "v4"])
    _run([
        "experiments/scripts/compute_annotation_agreement.py",
        "--annotation-root", "experiments/annotations/protocol_v4/inter_annotator",
        "--output", "experiments/annotations/protocol_v4/annotation_agreement.json",
    ])
    _run(["experiments/scripts/build_protocol_v4_formal_assets.py"])

    include = _included_assets()
    optional = [
        args.results_root / "protocol_v4_activation_dry_run/reports/activation_contract_20260810/mechanism_activation_audit.json",
        args.results_root / "protocol_v4_external_pilot/reports/protocol_v4_external_pilot_20260810/protocol_v4_agent_pilot.strict_audit.json",
        args.results_root / "protocol_v4_external_pilot/reports/protocol_v4_external_pilot_20260810/protocol_v4_readiness_audit.json",
        args.results_root / "reports/dev/generated_experiment_summary.md",
        args.results_root / "reports/dev/statistics_summary.json",
        args.results_root / "reports/dev/significance_summary.json",
    ]
    optional.extend(sorted((args.results_root / "protocol_v4_external_pilot_after_api_ok/reports").glob("**/*.json")))
    optional.extend(sorted((args.results_root / "protocol_v4_external_pilot_after_api_ok/raw_traces").glob("**/*.json")))
    optional.extend(sorted((args.results_root / "tables/dev").glob("table_*.csv")))
    optional.extend(sorted((args.results_root / "protocol_v4_external_pilot/reports").glob("*/external_llm_seed_probe.json")))
    for directory in (
        "protocol_v4_agent_pilot_20260813",
        "protocol_v4_integrated_replay",
        "protocol_v4_longitudinal_audit",
        "protocol_v4_longitudinal_pilot_20260813_retry",
    ):
        optional.extend(sorted((args.results_root / directory).glob("**/*.json")))
    existing_optional = [str(path) for path in optional if path.exists()]
    _run([
        "experiments/scripts/build_artifact_bundle.py",
        "--matrix", "experiments/configs/protocol_v4_pilot_matrix.json",
        "--output", str(args.artifact_output),
        "--include", *include,
        *existing_optional,
    ])


if __name__ == "__main__":
    main()
