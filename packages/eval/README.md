# @xgzh/eval

AI 离线评测集（call-once-and-grade）。

## 评测维度

- **召回@5**：Top5 检索命中率（目标 ≥ 0.8）
- **答案准确度**：Judge 评估（GPT-4o）
- **幻觉率**：未在引用中出现的事实陈述比例
- **响应延迟**：P95 < 3s
- **成本**：单次平均 < ¥0.05

## 数据集结构（占位）

```
packages/eval/datasets/
├── diagnose-200.jsonl   # 200 条标注 query - golden answer
├── tldr-100.jsonl       # 100 条文章 TL;DR
└── crs-50.jsonl         # 50 条税务身份判定
```

## 跑评测

```bash
# Sprint 2 之后实现
uv run python -m eval.run --dataset diagnose-200
```
