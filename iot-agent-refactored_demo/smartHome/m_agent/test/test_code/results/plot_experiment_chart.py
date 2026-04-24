# -*- coding: utf-8 -*-
"""
Final single-column IEEE-style chart for LaTeX papers.

Features:
- Single-column friendly layout optimized for IEEEtran
- Truncate to 1 decimal place (no rounding) for bar labels
- Error bars use min/max over the 3 runs
- Style tuned for LaTeX single-column insertion
- Agent name mapping:
    ourAgent -> SARKA
    sage -> SAGE
    sasha -> sasha

Usage:
    from plot_experiment_chart import plot_experiment_chart

    plot_experiment_chart(
        base_dir=".",
        output_path="experiment_chart_ieee_single.pdf"
    )
"""

import json
import re
from pathlib import Path
from collections import OrderedDict

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import MultipleLocator


MODEL_FOLDER_TO_LABEL = OrderedDict([
    ("gpt-5-mini", "GPT-5-\nmini"),
    ("gpt-5-nano", "GPT-5-\nnano"),
    ("gemini-2.5-flash", "Gemini-\n2.5-flash"),
])

AGENT_FOLDERS = OrderedDict([
    ("ourAgent", "SARKA"),
    ("sage", "SAGE"),
    ("sasha", "sasha"),
])


def _extract_run_index(file_path):
    match = re.match(r"(\d+)_", file_path.name)
    if match:
        return int(match.group(1))
    return 10**9


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "t", "1", "yes", "y", "通过", "正确"}:
            return True
        if v in {"false", "f", "0", "no", "n", "未通过", "错误"}:
            return False
    return False


def _load_run_accuracy(json_path):
    with Path(json_path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    details = data.get("详细结果", [])
    if not isinstance(details, list):
        raise ValueError(f"'详细结果' 必须是列表：{json_path}")

    if not details:
        return 0.0

    pass_count = sum(1 for item in details if _to_bool(item.get("是否正确", False)))
    return pass_count / len(details)


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def _truncate_1_decimal(value):
    return int(value * 10) / 10


def collect_experiment_statistics(base_dir="."):
    base_dir = Path(base_dir)
    stats = OrderedDict()

    for agent_folder, legend_label in AGENT_FOLDERS.items():
        agent_dir = base_dir / agent_folder
        if not agent_dir.exists():
            raise FileNotFoundError(f"agent 目录不存在：{agent_dir}")

        stats[legend_label] = OrderedDict()

        for model_folder, model_label in MODEL_FOLDER_TO_LABEL.items():
            model_dir = agent_dir / model_folder
            if not model_dir.exists():
                raise FileNotFoundError(f"模型目录不存在：{model_dir}")

            json_files = sorted(
                model_dir.glob("*_smart_home_test_results.json"),
                key=_extract_run_index
            )[:3]

            run_accuracies = [_load_run_accuracy(p) for p in json_files]

            if not run_accuracies:
                mean_acc = 0.0
                err_low = 0.0
                err_high = 0.0
            else:
                mean_acc = _mean(run_accuracies)
                min_acc = min(run_accuracies)
                max_acc = max(run_accuracies)
                err_low = mean_acc - min_acc
                err_high = max_acc - mean_acc

            stats[legend_label][model_label] = {
                "runs": run_accuracies,
                "mean": mean_acc,
                "err_low": err_low,
                "err_high": err_high,
            }

    return stats


def _annotate_bars(ax, bars, means, err_highs):
    for bar, mean_value, err_high in zip(bars, means, err_highs):
        truncated = _truncate_1_decimal(mean_value)
        ax.text(
            bar.get_x() + bar.get_width() / 2,
            mean_value + err_high + 0.9,
            f"{truncated:.1f}%",
            ha="center",
            va="bottom",
            fontsize=6.0
        )


def plot_experiment_chart(base_dir=".", output_path="experiment_chart_ieee_single.pdf", title=None):
    stats = collect_experiment_statistics(base_dir=base_dir)

    model_labels = list(MODEL_FOLDER_TO_LABEL.values())
    legend_labels = list(AGENT_FOLDERS.values())

    x = np.arange(len(model_labels))
    width = 0.18

    plt.rcParams.update({
        "font.family": "serif",
        "font.serif": ["Times New Roman", "Times", "Nimbus Roman", "DejaVu Serif"],
        "font.size": 7,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "figure.dpi": 300,
        "savefig.dpi": 300,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })

    fig, ax = plt.subplots(figsize=(3.5, 3.15))
    fig.patch.set_facecolor("white")
    ax.set_facecolor("#EAEAF2")
    ax.set_axisbelow(True)

    colors = ["#7BA6C7", "#4F8DB8", "#3D6178"]

    for i, legend_label in enumerate(legend_labels):
        means = [stats[legend_label][model]["mean"] * 100 for model in model_labels]
        err_lows = [stats[legend_label][model]["err_low"] * 100 for model in model_labels]
        err_highs = [stats[legend_label][model]["err_high"] * 100 for model in model_labels]
        yerr = np.array([err_lows, err_highs])

        offset = (i - 1) * width
        bars = ax.bar(
            x + offset,
            means,
            width=width * 0.9,
            label=legend_label,
            color=colors[i],
            edgecolor=colors[i],
            linewidth=0.45,
            yerr=yerr,
            error_kw={
                "elinewidth": 0.75,
                "capsize": 2.2,
                "capthick": 0.75,
                "ecolor": "gray",
            },
            zorder=3
        )

        _annotate_bars(ax, bars, means, err_highs)

    if title:
        ax.set_title(title, pad=3, fontsize=8)

    ax.set_ylabel("Success Rate (%)", labelpad=1)
    ax.set_xlabel("")

    ax.set_xticks(x)
    ax.set_xticklabels(model_labels)

    ax.set_ylim(0, 100)
    ax.yaxis.set_major_locator(MultipleLocator(20))

    ax.grid(axis="y", linestyle="-", linewidth=0.65, color="white", alpha=0.85)
    ax.grid(axis="x", visible=False)

    for spine in ax.spines.values():
        spine.set_visible(False)

    ax.tick_params(axis="both", length=0, pad=1.0)

    legend = ax.legend(
        title="method",
        loc="lower center",
        bbox_to_anchor=(0.5, 1.06),
        ncol=3,
        frameon=False,
        borderpad=0.2,
        handlelength=1.2,
        labelspacing=0.2,
        columnspacing=0.8,
    )
    plt.setp(legend.get_title(), fontsize=7)

    plt.subplots_adjust(left=0.12, right=0.995, top=0.82, bottom=0.14)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Save format follows suffix; default is PDF.
    plt.savefig(
        output_path,
        facecolor="white",
        transparent=False,
        bbox_inches="tight",
        pad_inches=0.005
    )

    plt.close(fig)

    return str(output_path.resolve())


if __name__ == "__main__":
    chart_path = plot_experiment_chart(
        base_dir=".",
        output_path="./experiment_results/experiment_chart_ieee_single.pdf",
        title=None
    )
    print(f"Chart saved to: {chart_path}")
