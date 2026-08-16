# v4 真人双标注操作说明

## 状态边界

当前 `llm_assisted_annotation_report.json` 的状态是 `complete_llm_assisted`，model-model Cohen's kappa 为 `0.5455`。它不是人工双标注；在本流程完成前，`human_annotation_complete` 必须保持 `false`。

## 盲化分发

1. 研究协调者从 `annotation_task_pack.json` 取得 13 个场景清单和对应的 `inter_annotator/*.json` 空模板。
2. 标注者 A 仅获得场景原始输入和用户可观察结局，填写 `annotator_a`；标注者 B 获得同一盲化材料，填写 `annotator_b`。
3. 不向任一标注者提供系统名称、模型输出、工具调用、trace、MemoryRecord、evaluator ground truth、另一位标注者标签或 model-model 标注文件。
4. 两位标注者独立完成后，协调者才合并到同一个场景 JSON。缺失标签保持 `null`，不得用自动标签、gold 标签或系统输出补写。

## 裁决与计算

1. 仅在两位标签不一致时，将场景原始材料和两份匿名标签交给第三位裁决者。
2. 裁决者填写 `adjudication.status`、`adjudicator_id`、`final_label` 和 `rationale`；不应改写 A/B 的原始标签。
3. 完成后运行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 experiments/scripts/compute_annotation_agreement.py \
  --annotation-root experiments/annotations/protocol_v4/inter_annotator \
  --output experiments/annotations/protocol_v4/annotation_agreement.json
```

4. 只有报告为 `complete`、`pending_count=0`、`adjudication_required_count=0` 且人工 kappa 非空时，才能在论文中报告人工 Cohen's kappa。
