#!/usr/bin/env bash
# verify-deploy.sh — 一键校验 ECS 部署是否真的生效 (OPS-S10).
#
# 用法:
#   ./infra/verify-deploy.sh                     # 用 HEAD short sha 作为期望版本
#   ./infra/verify-deploy.sh 25db9eb             # 显式指定期望 sha
#   API_HOST=8.130.156.2 ./infra/verify-deploy.sh
#
# 环境变量:
#   API_HOST   生产 API host (默认 8.130.156.2)
#   API_PORT   生产 API port (默认 8000)
#   SSH_HOST   SSH 跳板地址 (默认 root@<API_HOST>)
#   DB_NAME    PG 库名 (默认 xgzh)
#
# 退出码:
#   0  全部 ✅
#   1  任一检查 ❌
#   2  环境不全 (缺 ssh / curl / jq 等)
#
# 设计:
# - 分 L1 (GH Actions 状态, 可选) / L2 (ECS 镜像 tag 比对) / L3 (API 行为) 三层
# - 任一层失败立即 exit 1 + 红色标记, 后续 step 仍尝试跑 (帮助看全貌)
# - 不需要 sudo / 不改服务器状态 — 纯只读
# - 适合 push 完 60s 后跑一次, CI green ≠ deploy ok 的兜底
#
# 历史踩坑覆盖:
# - ACR 缓存 latest tag (L2 比对 image digest)
# - .env IMAGE_TAG 没更新 (L2 grep .env)
# - 容器名冲突, compose up 没真重启 (L2 docker inspect StartedAt)
# - alembic migration 没跑 (L3 比对 alembic_head)
# - /healthz ok 但新 endpoint 路由没注册 (L3 探测新路径)

set -uo pipefail  # 不开 -e — 失败需要继续跑后续 check 给完整报告

# ─── 配置 ─────────────────────────────────────────────────────────

EXPECTED_SHA="${1:-}"
if [ -z "$EXPECTED_SHA" ]; then
    EXPECTED_SHA=$(git rev-parse --short=7 HEAD 2>/dev/null || echo "")
fi

API_HOST="${API_HOST:-api.xgzh.top}"
API_PORT="${API_PORT:-443}"
API_PROTO="${API_PROTO:-https}"
API_BASE="${API_PROTO}://${API_HOST}"
# port 不是 80/443 时显式加冒号 (运行在自定义端口的本地测试场景)
if [ "$API_PORT" != "443" ] && [ "$API_PORT" != "80" ]; then
    API_BASE="${API_PROTO}://${API_HOST}:${API_PORT}"
fi
# SSH host 仍走 IP (DNS 跟不上时不能阻塞运维)
SSH_HOST="${SSH_HOST:-root@8.130.156.2}"
DB_NAME="${DB_NAME:-xgzh}"

# 终端色 (兼容 CI 无 tty)
if [ -t 1 ]; then
    GREEN=$'\033[0;32m'
    RED=$'\033[0;31m'
    YELLOW=$'\033[1;33m'
    BLUE=$'\033[0;34m'
    BOLD=$'\033[1m'
    NC=$'\033[0m'
else
    GREEN=""; RED=""; YELLOW=""; BLUE=""; BOLD=""; NC=""
fi

PASS_COUNT=0
FAIL_COUNT=0

# ─── helpers ──────────────────────────────────────────────────────

# 检查必备工具; 缺则 exit 2
check_deps() {
    local missing=()
    for cmd in curl ssh; do
        command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
    done
    # jq 不是硬依赖 (脚本会 fallback 到 python3 -c json), 但有更舒服
    if [ ${#missing[@]} -gt 0 ]; then
        echo "${RED}ERROR: 缺少依赖: ${missing[*]}${NC}"
        echo "macOS: brew install ${missing[*]}"
        exit 2
    fi
}

# 通用 PASS/FAIL 打印 + 计数
pass() {
    echo "  ${GREEN}✅${NC} $1"
    PASS_COUNT=$((PASS_COUNT + 1))
}

fail() {
    echo "  ${RED}❌${NC} $1"
    FAIL_COUNT=$((FAIL_COUNT + 1))
}

info() {
    echo "  ${BLUE}ℹ${NC}  $1"
}

section() {
    echo ""
    echo "${BOLD}${BLUE}═══ $1 ═══${NC}"
}

# JSON field 提取 (优先 jq, 没装走 python)
json_field() {
    local json="$1"
    local field="$2"
    if command -v jq >/dev/null 2>&1; then
        echo "$json" | jq -r ".$field // \"\""
    else
        echo "$json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('$field',''))" 2>/dev/null || echo ""
    fi
}

# ─── L0: 环境 banner ──────────────────────────────────────────────

print_banner() {
    echo "${BOLD}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo "${BOLD}║          XGZH ECS 部署校验 (verify-deploy.sh)                ║${NC}"
    echo "${BOLD}╚══════════════════════════════════════════════════════════════╝${NC}"
    echo "  期望 sha:  ${BOLD}${EXPECTED_SHA:-<unset>}${NC}"
    echo "  API:       $API_BASE"
    echo "  SSH:       $SSH_HOST"
    echo "  DB:        $DB_NAME"
    if [ -z "$EXPECTED_SHA" ]; then
        echo "  ${YELLOW}⚠ 未指定期望 sha 也没在 git 仓库; L2 sha 比对会跳过${NC}"
    fi
}

# ─── L1: GitHub Actions 状态 (可选, 需 gh cli) ──────────────────

check_l1_gh_actions() {
    section "L1: GitHub Actions 最近一次 deploy"
    if ! command -v gh >/dev/null 2>&1; then
        info "未装 gh cli, 跳过 (装: brew install gh)"
        return
    fi
    if ! gh auth status >/dev/null 2>&1; then
        info "gh 未登录, 跳过"
        return
    fi
    local latest
    latest=$(gh run list --workflow=deploy.yml --limit 1 --json status,conclusion,headSha,createdAt 2>/dev/null || echo "[]")
    if [ "$latest" = "[]" ]; then
        info "没找到 deploy.yml 运行记录"
        return
    fi
    local status conclusion head_sha
    status=$(json_field "$(echo "$latest" | head -c 1024)" "[0].status" 2>/dev/null || echo "")
    if command -v jq >/dev/null 2>&1; then
        status=$(echo "$latest" | jq -r '.[0].status')
        conclusion=$(echo "$latest" | jq -r '.[0].conclusion')
        head_sha=$(echo "$latest" | jq -r '.[0].headSha' | cut -c1-7)
    else
        status=$(echo "$latest" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['status'])")
        conclusion=$(echo "$latest" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['conclusion'] or '')")
        head_sha=$(echo "$latest" | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['headSha'][:7])")
    fi
    info "最近 deploy: status=$status conclusion=$conclusion sha=$head_sha"
    if [ "$conclusion" = "success" ]; then
        pass "GitHub Actions deploy 成功"
    elif [ "$status" = "in_progress" ] || [ "$status" = "queued" ]; then
        fail "deploy 还在跑 (status=$status); 等完再校验"
    else
        fail "deploy conclusion=$conclusion (期望 success)"
    fi
    if [ -n "$EXPECTED_SHA" ] && [ "$head_sha" != "$EXPECTED_SHA" ]; then
        fail "GH 最近 deploy sha=$head_sha 与期望 $EXPECTED_SHA 不符"
    fi
}

# ─── L2: ECS 镜像 tag + 容器状态 ─────────────────────────────────

check_l2_ecs_image() {
    section "L2: ECS 上 IMAGE_TAG + 容器启动时间"
    local remote_output
    remote_output=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" '
cd /opt/xgzh 2>/dev/null || exit 70
IMAGE_TAG=$(grep -E "^IMAGE_TAG=" .env 2>/dev/null | cut -d= -f2)
echo "IMAGE_TAG_IN_ENV=$IMAGE_TAG"
# docker compose ps 输出: name, image, status
docker compose -f docker-compose.production.yml ps --format "json" 2>/dev/null | head -c 4096
echo ""
# api 容器的 inspect 信息 (启动时间 + 镜像 hash)
API_CONT=$(docker ps --format "{{.Names}}" | grep -E "^xgzh-api$" | head -1)
if [ -n "$API_CONT" ]; then
    docker inspect "$API_CONT" --format "STARTED_AT={{.State.StartedAt}}"
    docker inspect "$API_CONT" --format "IMAGE={{.Config.Image}}"
    docker inspect "$API_CONT" --format "IMAGE_ID={{.Image}}"
fi
' 2>&1) || {
        local rc=$?
        fail "SSH 连不上 $SSH_HOST (exit=$rc); 检查 ssh key / IP / firewall"
        return
    }
    # 解析远端输出
    local image_tag_env image_field image_id started_at
    image_tag_env=$(echo "$remote_output" | grep "^IMAGE_TAG_IN_ENV=" | cut -d= -f2)
    started_at=$(echo "$remote_output" | grep "^STARTED_AT=" | cut -d= -f2-)
    image_field=$(echo "$remote_output" | grep "^IMAGE=" | cut -d= -f2-)
    image_id=$(echo "$remote_output" | grep "^IMAGE_ID=" | cut -d= -f2- | cut -c8-19)  # sha256: 后取 12 字符

    info ".env IMAGE_TAG=$image_tag_env"
    info "容器 Image=$image_field"
    info "镜像 ID=$image_id"
    info "容器 Started=$started_at"

    if [ -z "$image_tag_env" ]; then
        fail ".env IMAGE_TAG 为空; sed 改 .env 步骤可能失败"
        return
    fi

    # 期望 sha 比对
    if [ -n "$EXPECTED_SHA" ]; then
        if [ "$image_tag_env" = "$EXPECTED_SHA" ]; then
            pass ".env IMAGE_TAG ($image_tag_env) 与期望 sha 一致"
        else
            fail ".env IMAGE_TAG=$image_tag_env ≠ 期望 $EXPECTED_SHA"
        fi
    fi

    # 容器 IMAGE 字段必须含 IMAGE_TAG
    if [ -n "$image_field" ] && [[ "$image_field" == *":$image_tag_env" ]]; then
        pass "容器 IMAGE 跟 .env IMAGE_TAG 对齐"
    elif [ -n "$image_field" ]; then
        fail "容器 IMAGE=$image_field 不含 .env tag=$image_tag_env (容器没真重启?)"
    else
        fail "未找到运行中的 xgzh-api 容器"
    fi

    # 启动时间检查: 期望 < 10min (60s deploy + 验证)
    if [ -n "$started_at" ]; then
        local started_epoch now_epoch age_min
        # macOS BSD date 跟 GNU date 语法不同; 走 python 兜底
        started_epoch=$(python3 -c "
from datetime import datetime
import sys
s = '$started_at'.replace('Z', '+00:00').split('.')[0]
try:
    dt = datetime.fromisoformat(s)
    print(int(dt.timestamp()))
except Exception:
    print(0)
" 2>/dev/null)
        now_epoch=$(date +%s)
        if [ "$started_epoch" -gt 0 ]; then
            age_min=$(( (now_epoch - started_epoch) / 60 ))
            if [ "$age_min" -lt 60 ]; then
                pass "容器 ${age_min}min 前启动 (新鲜)"
            elif [ "$age_min" -lt 1440 ]; then
                info "容器 ${age_min}min 前启动 (今天但不是这次部署)"
            else
                fail "容器 $((age_min / 1440))天前启动, 没真重启过"
            fi
        fi
    fi
}

# ─── L3: API 行为 + DB schema ────────────────────────────────────

check_l3_api_behavior() {
    section "L3: API 端点行为 + DB schema"

    # 3.1 /healthz
    local health
    health=$(curl -fsS --max-time 10 "$API_BASE/healthz" 2>/dev/null || echo "")
    if [ -n "$health" ]; then
        pass "/healthz 200 — $(echo "$health" | head -c 80)"
    else
        fail "/healthz 不通; 检查 ECS 8000 端口 / 容器健康"
        return  # 健康都没过, 后面 check 没意义
    fi

    # 3.2 /version (OPS-S10 新加)
    local version_json git_sha alembic_head started_at
    version_json=$(curl -fsS --max-time 10 "$API_BASE/version" 2>/dev/null || echo "")
    if [ -z "$version_json" ]; then
        fail "/version endpoint 404 — BE 还没合并 /version (本次部署的就是这个 PR? 那是预期)"
    else
        git_sha=$(json_field "$version_json" "git_sha")
        alembic_head=$(json_field "$version_json" "alembic_head")
        started_at=$(json_field "$version_json" "started_at")
        info "/version git_sha=$git_sha alembic_head=$alembic_head started_at=$started_at"
        if [ -n "$EXPECTED_SHA" ] && [ "$git_sha" = "$EXPECTED_SHA" ]; then
            pass "/version git_sha = 期望 sha"
        elif [ -n "$EXPECTED_SHA" ] && [ "$git_sha" = "unknown" ]; then
            fail "/version git_sha=unknown — Dockerfile ARG APP_GIT_SHA 没注入 (升级 deploy.yml build-args)"
        elif [ -n "$EXPECTED_SHA" ]; then
            fail "/version git_sha=$git_sha ≠ 期望 $EXPECTED_SHA"
        fi
        # alembic head 不为 unknown
        if [ -n "$alembic_head" ] && [ "$alembic_head" != "unknown" ]; then
            pass "alembic_head=$alembic_head (DB 已 upgrade)"
        else
            fail "alembic_head 拿不到 — DB 未跑 migration 或表不存在"
        fi
    fi

    # 3.3 关键 endpoint 烟雾 (Sprint 10 加的 admin/users; 之后 sprint 改这一行)
    local admin_users_code
    admin_users_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$API_BASE/api/v1/admin/users" 2>/dev/null || echo "000")
    if [ "$admin_users_code" = "401" ]; then
        pass "/api/v1/admin/users 401 (Sprint 10 路由已注册, 需 admin token)"
    elif [ "$admin_users_code" = "404" ]; then
        fail "/api/v1/admin/users 404 — 路由没注册, 旧镜像在跑"
    elif [ "$admin_users_code" = "000" ]; then
        fail "/api/v1/admin/users 请求超时/失败"
    else
        info "/api/v1/admin/users HTTP $admin_users_code (非预期 401, 但不一定是问题)"
    fi

    # 3.4 DB schema 烟雾: users.is_admin 列存在 (Sprint 10 alembic 0017)
    # 走 SSH; 如果 L2 SSH 已不通, 这里也跳 (重复 fail 无意义), 因为 /version 已确认 alembic_head
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$SSH_HOST" 'true' 2>/dev/null; then
        info "SSH 不通, 跳过 users.is_admin 列直查 (alembic_head 已在 /version 确认)"
        return
    fi
    local pg_check
    pg_check=$(ssh -o ConnectTimeout=10 -o BatchMode=yes "$SSH_HOST" "
docker exec -i xgzh-postgres psql -U xgzh -d $DB_NAME -tAc \"
    SELECT column_name FROM information_schema.columns
    WHERE table_name='users' AND column_name='is_admin';
\" 2>/dev/null
" 2>&1)
    if echo "$pg_check" | grep -q "^is_admin$"; then
        pass "users.is_admin 列存在 (alembic 0017 已 apply)"
    else
        fail "users.is_admin 列缺失 — alembic 没跑或 DB 是旧版"
    fi
}

# ─── 主流程 ──────────────────────────────────────────────────────

main() {
    check_deps
    print_banner
    check_l1_gh_actions
    check_l2_ecs_image
    check_l3_api_behavior

    echo ""
    echo "${BOLD}═══ 总结 ═══${NC}"
    echo "  ${GREEN}✅ PASS: $PASS_COUNT${NC}    ${RED}❌ FAIL: $FAIL_COUNT${NC}"
    if [ "$FAIL_COUNT" -eq 0 ]; then
        echo ""
        echo "${GREEN}${BOLD}🎉 部署校验全过! sha=$EXPECTED_SHA 已在 ECS 生效.${NC}"
        exit 0
    else
        echo ""
        echo "${RED}${BOLD}⚠  $FAIL_COUNT 项失败. 排查方向见 docs/bug/ 或上面具体行的提示.${NC}"
        exit 1
    fi
}

main "$@"
