"""离线评测脚手架 (BE-S2-009).

模块布局
========
- ``schema``     : EvalCase / EvalRunResult / 报告字段的 Pydantic 模型 + JSONL 读写
- ``metrics``    : 召回@5 / 幻觉率 / 综合分等指标的"纯函数"实现, 不调任何 IO
- ``judge``      : LLM-as-judge 调度 (调用 ``app.adapters.llm_client.chat`` 跑 1-5 分)
- ``runner``     : 三种评测模式编排 (keyword / retrieval / end_to_end)
- ``reporter``   : 把 RunReport 渲染成 markdown / json 报告
- ``cli``        : ``python -m evals.cli``  CLI 入口, ``make eval-sprint2`` 调它

放置位置
========
评测脚手架是 *开发期 + CI 工具*, 不是生产 runtime, 故放 ``apps/api/evals/`` 而非
``apps/api/app/``: hatchling build 默认只打包 ``app/``, evals 不会进 wheel /
Docker 镜像.

数据集放 ``apps/api/evals/dataset/sprint2_80q.jsonl`` (合成 + 公开 IPO query),
报告输出到 ``apps/api/evals/reports/`` (gitignored).
"""
