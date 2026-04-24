import json
from collections import defaultdict
from pathlib import Path
from openpyxl import Workbook


def load_tags(tags_file: Path):
    """加载标签信息"""
    with tags_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    case_tags_map = {}
    tag_total_single = defaultdict(int)

    for item in data.get("详细结果", []):
        case_id = item["用例编号"]
        tags = item.get("所属标签", [])
        case_tags_map[case_id] = tags
        for tag in tags:
            tag_total_single[tag] += 1

    ordered_tags = data.get("标签值", list(tag_total_single.keys()))
    return case_tags_map, dict(tag_total_single), ordered_tags


def load_result_map(result_file: Path):
    """加载单次实验结果"""
    with result_file.open("r", encoding="utf-8") as f:
        data = json.load(f)

    result_map = {}
    for item in data.get("详细结果", []):
        result_map[item["用例编号"]] = item.get("是否正确", False)

    return result_map


def save_to_excel(final_output, ordered_tags, output_file: Path):
    """保存各模型标签成功率到 Excel"""
    wb = Workbook()
    ws = wb.active
    ws.title = "tag_pass_statistics"

    # 表头
    header = ["模型"] + ordered_tags
    ws.append(header)

    # 数据行
    for model_name, stats in final_output.items():
        row = [model_name]
        for tag in ordered_tags:
            row.append(stats[tag]["成功率"])
        ws.append(row)

    wb.save(output_file)


def main():
    base_dir = Path(".")
    tags_file = base_dir / "test_tags.json"
    ouragent_dir = base_dir / "ourAgent"
    output_dir = base_dir / "tags_results"

    output_dir.mkdir(parents=True, exist_ok=True)

    json_output_file = output_dir / "tag_pass_statistics.json"
    excel_output_file = output_dir / "tag_pass_statistics.xlsx"

    case_tags_map, tag_total_single, ordered_tags = load_tags(tags_file)

    final_output = {}

    # 遍历模型
    for model_dir in sorted(ouragent_dir.iterdir()):
        if not model_dir.is_dir():
            continue

        model_name = model_dir.name
        tag_pass = defaultdict(int)
        valid_runs = 0

        # 遍历3次实验并汇总
        for i in range(1, 4):
            result_file = model_dir / f"{i}_smart_home_test_results.json"
            if not result_file.exists():
                continue

            valid_runs += 1
            result_map = load_result_map(result_file)

            for case_id, tags in case_tags_map.items():
                is_pass = result_map.get(case_id, False)
                if is_pass:
                    for tag in tags:
                        tag_pass[tag] += 1

        # 统计单个模型的标签通过情况
        model_stats = {}
        for tag in ordered_tags:
            total = tag_total_single.get(tag, 0) * valid_runs
            passed = tag_pass.get(tag, 0)
            rate = passed / total if total > 0 else 0.0

            model_stats[tag] = {
                "总数": total,
                "通过数": passed,
                "成功率": f"{rate:.2%}"
            }

        final_output[model_name] = model_stats

    # 保存 JSON
    with json_output_file.open("w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=4)

    # 保存 Excel
    save_to_excel(final_output, ordered_tags, excel_output_file)

    # 控制台打印
    for model, stats in final_output.items():
        print(f"\n================ {model} ================")
        for tag, info in stats.items():
            print(
                f"{tag}: 总数={info['总数']}, "
                f"通过数={info['通过数']}, "
                f"成功率={info['成功率']}"
            )

    print(f"\n已生成 JSON: {json_output_file}")
    print(f"已生成 Excel: {excel_output_file}")


if __name__ == "__main__":
    main()
