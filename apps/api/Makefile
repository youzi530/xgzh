# xgzh-api · Sprint 1.5 收尾包 · Makefile (开发体验工具集)
#
# 目标:
# 1. 把 README 里散落的 "测试库初始化 / 跑 e2e / 跑全部" 操作沉淀成一行命令,
#    让新人 onboard 5 分钟可跑通, CI 也用同一组 target 统一入口.
# 2. 严格区分 *单元测试* (无 DB, 永远跑) 和 *集成测试* (依赖 PG/Redis, 显式跑),
#    避免新人误把"集成测试 skip"当成"测试通过".
# 3. 测试库 URL / dev 库 URL 强校验, 防止误对开发库做 DROP CASCADE.
#
# 使用约定:
# - 所有 target 必须可在 ``apps/api/`` 目录下直接 ``make <target>`` 跑通.
# - 不要在 target 里硬编码 password (`postgres://user:pass@`); 走 .env 或环境变量.
# - DB target 默认假定 ``infra/docker-compose.yml`` 已启 ``xgzh-postgres`` + ``xgzh-redis``,
#   或本地 Homebrew Postgres 14 + Redis 7 跑着.

.PHONY: help test test-db-init test-unit test-e2e test-all lint typecheck \
        eval-sprint2-smoke eval-sprint2-retrieval eval-sprint2 eval-sprint2-judge

# 默认 dev / test 库 DSN; 用户可以通过 ``make TEST_DATABASE_URL=...  test-e2e`` 覆盖
DEV_DB_URL ?= postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh
TEST_DB_URL ?= postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh_test

help:  ## 显示所有可用 target (默认目标)
	@echo "xgzh-api Makefile · target 列表:"
	@grep -E '^[a-zA-Z0-9_-]+:.*?##' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

test-db-init:  ## 创建测试库 xgzh_test (幂等; 已存在则 noop)
	@echo "==> 创建测试库 xgzh_test (如已存在则跳过)..."
	@psql -U xgzh -h localhost -d postgres -tAc \
		"SELECT 1 FROM pg_database WHERE datname='xgzh_test'" | grep -q 1 \
		|| psql -U xgzh -h localhost -d postgres -c 'CREATE DATABASE xgzh_test'
	@echo "==> 安装 pgcrypto extension (gen_random_uuid() 需要)..."
	@psql -U xgzh -h localhost -d xgzh_test -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto' >/dev/null
	@echo "==> 测试库就绪: $(TEST_DB_URL)"
	@echo "    下一步: make test-e2e"

test-unit:  ## 跑单元测试 (不依赖 PG; 集成测试自动 skip)
	uv run pytest tests/ --ignore=tests/integration -v

test-e2e:  ## 跑集成 e2e 测试 (依赖 PG; 必须先 make test-db-init)
	XGZH_TEST_DATABASE_URL='$(TEST_DB_URL)' uv run pytest tests/integration/ -v

test-all:  ## 跑所有测试 (单元 + 集成); 等价于 CI 全量
	XGZH_TEST_DATABASE_URL='$(TEST_DB_URL)' uv run pytest tests/ -v

test:  ## test-all 别名
	@$(MAKE) test-all

lint:  ## ruff 静态检查
	uv run ruff check

typecheck:  ## mypy 类型检查
	uv run mypy app

# ─── BE-S2-009 离线评测脚手架 ────────────────────────────────────────────
# 三档跑法:
#   eval-sprint2-smoke    : 无 IO, 仅校验 80 条数据集 schema 正确 (CI 默认)
#   eval-sprint2-retrieval: 走 PG, 算召回@5 (BE-S2-005 hybrid_search baseline)
#   eval-sprint2          : 走 PG + LLM, 算召回@5 + 字符级幻觉 (不调 judge)
#   eval-sprint2-judge    : 全量 + LLM-as-judge (1-5 分语义评分; 最贵)

eval-sprint2-smoke:  ## 评测集 80 条 schema smoke (无 PG / LLM)
	uv run python -m evals.cli --mode keyword --no-write

eval-sprint2-retrieval:  ## 评测召回@5 (需 PG; LLM 不依赖)
	uv run python -m evals.cli --mode retrieval

eval-sprint2:  ## 端到端评测 (需 PG + LLM key; 不调 judge)
	uv run python -m evals.cli --mode end_to_end

eval-sprint2-judge:  ## 端到端 + LLM-as-judge (需 PG + LLM key; 最贵)
	uv run python -m evals.cli --mode end_to_end --use-judge
