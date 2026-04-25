-- XGZH PostgreSQL 初始化脚本
-- 仅用于本地开发环境（生产用 Alembic 迁移管理）

CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "vector";

-- 仅作占位提示，真实表结构由 alembic 管理
COMMENT ON DATABASE xgzh IS 'XGZH (新股智汇) - dev database';
