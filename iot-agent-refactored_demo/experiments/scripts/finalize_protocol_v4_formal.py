from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.metrics.core import task_metrics


SYSTEMS = ["Ours", "B0", "B1", "B2", "B3", "B4", "B5"]
SUMMARY_METRICS = [
    "TSR",
    "Control Final-State TSR",
    "Query Answer Accuracy",
    "Automation Decision Accuracy",
    "WDR",
    "Unsafe Action Rate",
    "Necessary Clarification Rate",
    "Unnecessary Clarification Rate",
    "Prompt Tokens",
    "Completion Tokens",
    "end_to_end_latency_ms",
    "SRR",
    "UC",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _canonical_traces(root: Path) -> list[dict]:
    traces = []
    for path in sorted(root.glob("raw_traces/**/*.json")):
        if path.name.endswith(".maintenance.json"):
            continue
        trace = _load(path)
        if trace.get("evaluation_protocol") == "v4" and trace.get("agent_backend") == "external_llm":
            traces.append(trace)
    return traces


def _summarize_workload(name: str, root: Path) -> dict:
    traces = _canonical_traces(root)
    by_system: dict[str, list[dict]] = defaultdict(list)
    failures = Counter()
    for trace in traces:
        by_system[str(trace.get("system_id"))].append(task_metrics(trace))
        if not trace.get("external_task_success"):
            failures[(str(trace.get("system_id")), str(trace.get("scenario_id")))] += 1
    systems = []
    for system_id in SYSTEMS:
        rows = by_system.get(system_id, [])
        metric_summary = {}
        for metric in SUMMARY_METRICS:
            values = [float(row[metric]) for row in rows if row.get(metric) is not None]
            metric_summary[metric] = {"mean": _mean(values), "eligible_count": len(values)}
        systems.append({"system_id": system_id, "unit_count": len(rows), "metrics": metric_summary})
    return {
        "workload": name,
        "unit_count": len(traces),
        "systems": systems,
        "failure_counts": [
            {"system_id": system, "scenario_id": scenario, "count": count}
            for (system, scenario), count in sorted(failures.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def _repair_summary(behavioral_root: Path) -> dict:
    repair_root = next(iter(behavioral_root.glob("reports/*/failed_attempts")), None)
    rows = []
    if repair_root:
        for manifest_path in sorted(repair_root.glob("**/manifest.json")):
            manifest = _load(manifest_path)
            rows.append(
                {
                    "path": str(manifest_path.relative_to(REPO_ROOT)),
                    "system_id": manifest.get("system_id"),
                    "scenario_id": manifest.get("scenario_id"),
                    "replicate_id": manifest.get("seed"),
                    "agent_failures": manifest.get("agent_failures", []),
                    "partial_api_calls": int(manifest.get("agent_api_call_count", 0) or 0),
                    "partial_total_tokens": int((manifest.get("agent_usage_totals") or {}).get("total_tokens", 0) or 0),
                }
            )
    return {
        "repair_unit_count": len(rows),
        "partial_api_calls": sum(row["partial_api_calls"] for row in rows),
        "partial_total_tokens": sum(row["partial_total_tokens"] for row in rows),
        "rows": rows,
    }


def _tsr_comparisons(formal_statistics: dict) -> list[dict]:
    rows = []
    for comparison in formal_statistics.get("comparisons", []):
        metric = next(item for item in comparison["metrics"] if item["metric"] == "TSR")
        rows.append({"baseline": comparison["system_id"], **metric})
    return rows


def _write_csv(path: Path, workload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["system_id", "unit_count", *SUMMARY_METRICS])
        for row in workload["systems"]:
            writer.writerow(
                [row["system_id"], row["unit_count"]]
                + [row["metrics"][metric]["mean"] for metric in SUMMARY_METRICS]
            )


def _format(value: float | None, digits: int = 4) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def _write_markdown(path: Path, report: dict) -> None:
    lines = [
        "# SAE Memory System v4 正式实验封版摘要",
        "",
        f"- 冻结 revision：`{report['git_revision']}`",
        "- 标注门禁：`complete_llm_assisted`；这是模型-模型标注与第三模型裁决，不是人工 Cohen's kappa。",
        "- Claim 边界：behavioral 测量 experiments MemoryService + plan-only Agent adapter，不代表完整 smartHome 产品 runtime。",
        "",
    ]
    for workload in report["workloads"]:
        lines.extend([
            f"## {workload['workload']}",
            "",
            f"Canonical units：`{workload['unit_count']}`。",
            "",
            "| System | Units | TSR | WDR | Unsafe | Prompt tokens | Latency ms |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ])
        for row in workload["systems"]:
            metrics = row["metrics"]
            lines.append(
                f"| {row['system_id']} | {row['unit_count']} | {_format(metrics['TSR']['mean'])} | "
                f"{_format(metrics['WDR']['mean'])} | {_format(metrics['Unsafe Action Rate']['mean'])} | "
                f"{_format(metrics['Prompt Tokens']['mean'], 1)} | {_format(metrics['end_to_end_latency_ms']['mean'], 1)} |"
            )
        lines.append("")
    lines.extend([
        "## Behavioral TSR paired comparisons",
        "",
        "| Baseline | Ours-baseline | 95% CI | Holm p | Paired n |",
        "|---|---:|---:|---:|---:|",
    ])
    for row in report["behavioral_tsr_comparisons"]:
        lines.append(
            f"| {row['baseline']} | {_format(row['ours_minus_baseline'])} | "
            f"[{_format(row['ci_low'])}, {_format(row['ci_high'])}] | "
            f"{row['holm_adjusted_p']:.3g} | {row['paired_eligible_count']} |"
        )
    repair = report["transport_repairs"]
    lines.extend([
        "",
        "## Transport repairs",
        "",
        f"Canonical strict/trace 审计均无 transport failure；另保留 `{repair['repair_unit_count']}` 个技术失败尝试，"
        f"其中 partial usage 为 `{repair['partial_api_calls']}` calls / `{repair['partial_total_tokens']}` tokens。"
        "这些尝试未进入性能分母，完整 canonical unit 在相同 revision 下恢复执行。",
        "",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description="Finalize protocol-v4 formal artifacts without model calls.")
    parser.add_argument("--behavioral-root", type=Path, required=True)
    parser.add_argument("--longitudinal-root", type=Path, required=True)
    parser.add_argument("--robustness-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.behavioral_root = args.behavioral_root.resolve()
    args.longitudinal_root = args.longitudinal_root.resolve()
    args.robustness_root = args.robustness_root.resolve()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)

    behavioral_report_root = next(args.behavioral_root.glob("reports/*"))
    longitudinal_report_root = next(args.longitudinal_root.glob("reports/*"))
    robustness_report_root = next(args.robustness_root.glob("reports/*"))
    formal_statistics_path = behavioral_report_root / "formal_statistics.json"
    formal_statistics = _load(formal_statistics_path)
    strict_paths = [
        behavioral_report_root / "protocol_v4_formal_agent.strict_audit.json",
        longitudinal_report_root / "protocol_v4_formal_longitudinal.strict_audit.json",
        robustness_report_root / "protocol_v4_robustness.strict_audit.json",
    ]
    strict = [_load(path) for path in strict_paths]
    if any(item.get("status") != "pass" for item in strict):
        raise SystemExit("all strict audits must pass before finalization")
    revisions = {revision for item in strict for revision in item.get("git_revisions", {})}
    if len(revisions) != 1:
        raise SystemExit("formal workloads must share one git revision")

    workloads = [
        _summarize_workload("behavioral", args.behavioral_root),
        _summarize_workload("longitudinal", args.longitudinal_root),
        _summarize_workload("robustness", args.robustness_root),
    ]
    report = {
        "protocol": "v4",
        "status": "formal_results_ready_with_llm_assisted_annotation_deviation",
        "git_revision": next(iter(revisions)),
        "annotation_status": "complete_llm_assisted",
        "human_annotation_complete": False,
        "human_cohen_kappa": None,
        "claim_scope": "experiments MemoryService plus plan-only Agent adapter",
        "full_smarthome_runtime_validated": False,
        "workloads": workloads,
        "behavioral_tsr_comparisons": _tsr_comparisons(formal_statistics),
        "guard_diagnostics": formal_statistics.get("guard_diagnostics"),
        "transport_repairs": _repair_summary(args.behavioral_root),
        "canonical_usage": {
            "api_calls": sum(item["usage_summary"]["agent_api_call_count"] for item in strict),
            "total_tokens": sum(item["usage_summary"]["agent_total_tokens"] for item in strict),
        },
    }
    json_path = args.output / "formal_results_summary.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for workload in workloads:
        _write_csv(args.output / f"{workload['workload']}_system_metrics.csv", workload)
    markdown_path = args.output / "formal_results_summary.md"
    _write_markdown(markdown_path, report)

    pinned = [
        json_path,
        markdown_path,
        *(args.output / f"{name}_system_metrics.csv" for name in ("behavioral", "longitudinal", "robustness")),
        formal_statistics_path,
        *strict_paths,
        behavioral_report_root / "trace_audit_final.json",
        longitudinal_report_root / "trace_audit_final.json",
        robustness_report_root / "trace_audit_final.json",
        REPO_ROOT / "experiments/configs/protocol_v4_formal_agent_matrix.json",
        REPO_ROOT / "experiments/configs/protocol_v4_formal_longitudinal_matrix.json",
        REPO_ROOT / "experiments/configs/protocol_v4_formal_robustness_matrix.json",
        REPO_ROOT / "experiments/annotations/protocol_v4/llm_assisted_annotation_report.json",
        REPO_ROOT / "experiments/scripts/finalize_protocol_v4_formal.py",
        REPO_ROOT / "docs/v4正式实验协议与就绪清单.md",
        REPO_ROOT / "docs/v4预运行门禁报告-2026-08-14.md",
        REPO_ROOT / "docs/实验结果摘要.md",
        REPO_ROOT / "docs/实验实现进展.md",
        REPO_ROOT / "docs/WORKLOG.md",
    ]
    repair_root = next(iter(args.behavioral_root.glob("reports/*/failed_attempts")), None)
    if repair_root:
        pinned.extend(sorted(repair_root.glob("**/*")))
    pinned.extend(
        args.output / name
        for name in (
            "ingestion_audit.json",
            "longitudinal_history40_audit.json",
            "longitudinal_history80_audit.json",
            "llm_assisted_annotation_audit.json",
            "preflight_audit_recheck.json",
        )
    )
    files = []
    for path in pinned:
        if path.is_file():
            files.append({"path": str(path.relative_to(REPO_ROOT)), "sha256": _sha256(path), "size": path.stat().st_size})
    manifest = {
        "bundle_version": "protocol-v4-formal-bundle-v1",
        "git_revision": report["git_revision"],
        "status": report["status"],
        "files": files,
        "rebuild_command": "PYTHONDONTWRITEBYTECODE=1 python3 experiments/scripts/finalize_protocol_v4_formal.py ...",
    }
    manifest_path = args.output / "artifact_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(manifest_path)


if __name__ == "__main__":
    main()
