"""一次性 / 周期性运维脚本入口 (BE-S3-007 起).

各脚本独立可运行 (``python -m scripts.seed_brokers``); 不属于 ``app/`` 包的
业务代码, 但复用 ``app.db`` / ``app.cache`` 等基础设施. 与 alembic migration
区分:

- migration: schema 变更, 一次性, 由 alembic 跟踪版本; 不写业务数据
- scripts: 业务种子数据 / 数据回填 / 一次性 cron, 写业务数据; 幂等可重跑
"""
