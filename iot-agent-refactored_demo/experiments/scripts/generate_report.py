from __future__ import annotations

from datetime import datetime
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments.scripts._artifact_paths import configured_run_id, preferred_run_ids, result_stage, results_root


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_rows() -> list[dict]:
    rows: list[dict] = []
    root = results_root() / "aggregated_metrics"
    wanted_runs = preferred_run_ids()
    for summary_path in sorted(root.rglob("metrics.summary.json")):
        parts = summary_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        if run_id not in wanted_runs:
            continue
        rows.append(
            {
                "run_id": run_id,
                "system_id": system_id,
                "planner_mode": planner_mode,
                "metrics": _load_json(summary_path),
                "source": "summary",
            }
        )
    for metrics_path in sorted(root.rglob("metrics.json")):
        if metrics_path.with_name("metrics.summary.json").exists():
            continue
        parts = metrics_path.parts
        run_id, system_id, planner_mode = parts[-4], parts[-3], parts[-2]
        if run_id not in wanted_runs:
            continue
        rows.append(
            {
                "run_id": run_id,
                "system_id": system_id,
                "planner_mode": planner_mode,
                "metrics": _load_json(metrics_path),
                "source": "single",
            }
        )
    return rows


def _metric_text(payload: dict, key: str) -> str:
    value = payload.get(key)
    if value is None:
        return "-"
    if isinstance(value, dict):
        mean = value.get("mean")
        low = value.get("ci_low")
        high = value.get("ci_high")
        if mean is None:
            return "-"
        if low is None or high is None:
            return f"{mean:.4f}"
        return f"{mean:.4f} [{low:.4f}, {high:.4f}]"
    if isinstance(value, (int, float)):
        return f"{value:.4f}"
    return str(value)


def _number_text(value, default: float = 0.0) -> str:
    if value is None:
        return "N/A"
    if not isinstance(value, (int, float)):
        value = default
    return f"{value:.4f}"


def _markdown_table(rows: list[dict], keys: list[str]) -> str:
    header = ["run_id", "system_id", "planner_mode", *keys]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        metrics = row["metrics"]
        values = [row["run_id"], row["system_id"], row["planner_mode"]]
        values.extend(_metric_text(metrics, key) for key in keys)
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def _significance_table(path: Path) -> str:
    if not path.exists():
        return "当前没有比较型统计摘要。"
    rows = _load_json(path)
    header = [
        "run_id",
        "system_id",
        "planner_mode",
        "TSR_delta_vs_ours",
        "TSR_cohen_d",
        "TSR_holm_p",
        "WDR_delta_vs_ours",
        "CB_delta_vs_ours",
        "ECE_delta_vs_ours",
    ]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * len(header)) + " |",
    ]
    for row in rows:
        metrics = row["metrics"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["run_id"],
                    row["system_id"],
                    row["planner_mode"],
                    _number_text(metrics.get('TSR', {}).get('delta_mean_vs_ours')),
                    _number_text(metrics.get('TSR', {}).get('cohen_d')),
                    _number_text(metrics.get('TSR', {}).get('holm_adjusted_p'), 1.0),
                    _number_text(metrics.get('WDR', {}).get('delta_mean_vs_ours')),
                    _number_text(metrics.get('CB', {}).get('delta_mean_vs_ours')),
                    _number_text(metrics.get('ECE', {}).get('delta_mean_vs_ours')),
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def _seed_count(run_id: str, system_id: str, planner_mode: str) -> int:
    path = results_root() / "aggregated_metrics" / run_id / system_id / planner_mode / "metrics.by_seed.json"
    if not path.exists():
        return 0
    return len(json.loads(path.read_text(encoding="utf-8")))


def _load_optional(path: Path, default):
    return _load_json(path) if path.exists() else default


def main():
    rows = _collect_rows()
    report_path = REPO_ROOT / "docs" / "实验结果摘要.md"
    report_root = results_root() / "reports" / result_stage()
    significance_path = report_root / "significance_summary.json"
    generated_on = datetime.now().strftime("%A, %B %d, %Y")

    preferred_runs = preferred_run_ids()
    b0 = next(
        (
            row for row in rows
            if row["run_id"] == configured_run_id("baseline")
            and row["system_id"] == "B0"
            and row["planner_mode"] == "oracle"
        ),
        None,
    )
    if b0:
        b0_cb = (b0["metrics"].get("CB") or {}).get("mean")
        b0_tsr = (b0["metrics"].get("TSR") or {}).get("mean")
        if b0_cb and b0_tsr:
            for row in rows:
                if row["planner_mode"] != "oracle":
                    continue
                cb = (row["metrics"].get("CB") or {}).get("mean")
                tsr = (row["metrics"].get("TSR") or {}).get("mean")
                if isinstance(cb, (int, float)) and isinstance(tsr, (int, float)):
                    row["metrics"]["CE"] = {
                        "mean": ((b0_cb - cb) / b0_cb) * (tsr / b0_tsr),
                        "ci_low": None,
                        "ci_high": None,
                        "sampling_unit": "scenario",
                    }
    primary_rows = [row for row in rows if row["run_id"] in preferred_runs]
    appendix_rows = [row for row in rows if row["run_id"] not in preferred_runs]

    ours_rows = [
        row for row in primary_rows
        if row["system_id"] == "Ours" and row["run_id"] in {configured_run_id("oracle"), configured_run_id("agent")}
    ]
    baseline_rows = [row for row in primary_rows if row["run_id"] == configured_run_id("baseline")]
    ablation_rows = [row for row in primary_rows if row["run_id"] == configured_run_id("ablation")]
    appendix_rows = sorted(appendix_rows, key=lambda row: (row["run_id"], row["system_id"], row["planner_mode"]))
    formal_stage = result_stage().startswith("formal")
    audit = _load_optional(report_root / "artifact_audit.json", {})
    annotation = _load_optional(REPO_ROOT / "experiments" / "annotations" / "annotation_agreement.json", {})
    agent_manifest = _load_optional(
        results_root()
        / "reports"
        / configured_run_id("agent")
        / "Ours"
        / "agent"
        / "manifest.json",
        {},
    )
    if formal_stage:
        oracle_count = _seed_count(configured_run_id("oracle"), "Ours", "oracle")
        agent_count = _seed_count(configured_run_id("agent"), "Ours", "agent")
        baseline_count = _seed_count(configured_run_id("baseline"), "B0", "oracle")
        ablation_count = _seed_count(configured_run_id("ablation"), "-Decay", "oracle")
        if oracle_count >= 30 and agent_count >= 20 and baseline_count >= 30 and ablation_count >= 20:
            stage_note = (
                f"- 执行重复数已达到协议值（Oracle={oracle_count}，Agent={agent_count}，"
                f"Baseline={baseline_count}，Ablation={ablation_count}）。Oracle 的统计单位是场景而不是重复 seed。"
            )
        else:
            stage_note = (
                f"- 当前 formal 结果仍是预览数据（Oracle={oracle_count}，Agent={agent_count}，"
                f"Baseline={baseline_count}，Ablation={ablation_count}），在 seed 数未达标前不得写入论文主结果。"
            )
    else:
        stage_note = "- 目前结果仍属于开发态结果，主要用于验证主线、场景和统计链路是否成立。"

    if formal_stage and audit.get("status") == "pass":
        scope_note = (
            "自动化工件审计已通过。Oracle 受控实验可作为确定性 benchmark 写入论文主表；"
            "Agent 结果若标记为 heuristic_fallback，只能作为探索性结果。"
        )
    else:
        scope_note = "当前结果尚未通过正式工件审计，只能作为开发或候选结果。"

    content = [
        "# 实验结果摘要",
        "",
        f"> 生成日期：{generated_on}",
        "",
        "## 1. 说明",
        "",
        f"本报告基于当前仓库 `{results_root() / 'aggregated_metrics'}` 下已有结果自动汇总。",
        "本报告优先展示配置化主结果（`configured_*`），其余开发态/调试态运行放在附录。",
        scope_note,
        f"Agent 结果分类：`{agent_manifest.get('result_classification', 'unknown')}`；后端：`{agent_manifest.get('agent_backends', [])}`。",
        f"人工双标注状态：`{annotation.get('status', 'unknown')}`，Cohen's κ：`{annotation.get('cohen_kappa')}`。",
        "",
        "## 2. Ours 结果概览",
        "",
        _markdown_table(
            ours_rows,
            ["TSR", "State TSR", "WDR", "CB", "CE", "PM", "UAA", "UC", "MP", "DMR", "RRR", "Estimated Context Efficiency", "ECE"],
        ) if ours_rows else "暂无 Ours 结果。",
        "",
        "## 3. Baseline 结果概览",
        "",
        _markdown_table(
            baseline_rows,
            ["TSR", "State TSR", "WDR", "CB", "CE", "PM", "Estimated Context Efficiency", "ECE"],
        ) if baseline_rows else "暂无 Baseline 结果。",
        "",
        "## 4. Ablation 结果概览",
        "",
        _markdown_table(
            ablation_rows,
            ["TSR", "WDR", "CB", "UC", "MP", "DMR", "RRR", "ECE"],
        ) if ablation_rows else "暂无 Ablation 结果。",
        "",
        "## 5. 当前结论",
        "",
        f"- 截至 {generated_on}，实验主线已经可以运行 `Ours / baseline / ablation` 三类系统。",
        "- `Estimated Prompt Tokens` / `Estimated Maintenance Tokens` 是确定性 proxy，不是真实 LLM API token usage。",
        f"- 自动化工件审计状态：`{audit.get('status', 'missing')}`；可确认范围：`{audit.get('confirmatory_scope', 'none')}`。",
        stage_note,
        "",
        "## 6. 相对 Ours 的比较摘要",
        "",
        _significance_table(significance_path),
        "",
        "## 7. 结果目录",
        "",
        f"- 聚合指标：`{results_root() / 'aggregated_metrics'}`",
        f"- 表格：`{results_root() / 'tables' / result_stage()}`",
        f"- 图表：`{results_root() / 'figures' / result_stage()}`",
        f"- 报告：`{results_root() / 'reports'}`",
        "",
        "## 8. 附录：开发态/调试态运行",
        "",
        _markdown_table(
            appendix_rows,
            ["TSR", "WDR", "CB", "PM", "UAA", "Estimated Context Efficiency", "ECE"],
        ) if appendix_rows else "当前没有附录结果。",
        "",
    ]
    report_path.write_text("\n".join(content), encoding="utf-8")
    print(report_path)


if __name__ == "__main__":
    main()
