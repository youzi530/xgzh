#!/usr/bin/env bash
# QA-S4-002 一键起后端 + H5 + 自检的脚本。
#
# 用法:
#   ./run_journey.sh                    # 检查 + 起服务 + 等就绪
#   ./run_journey.sh --restart-h5       # 强制重启 H5(pages.json 变更后必用)
#   ./run_journey.sh --check-only       # 只跑健康自检, 不起服务
#
# 跑完后:
#   - http://127.0.0.1:8000  ← API
#   - http://localhost:5173/ ← H5
#   随后用 cursor browser-use MCP 按 test_user_journey.md 顺序跑脚本.
#
# 依赖:
#   - uv (后端 venv)
#   - pnpm + node (前端)
#   - lsof (端口检测; macOS / Linux 默认装)
#   - curl
#
# 退出码:
#   0  全绿
#   1  前置依赖缺失 / 端口占用 + 不重启
#   2  后端起不来
#   3  H5 起不来
#   4  数据自检失败 (DB 没回填 / 路由没注册)

set -euo pipefail

# ----- 路径 / 颜色 -----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../../../.." && pwd)"   # xgzh/
API_DIR="$REPO_ROOT/apps/api"
MP_DIR="$REPO_ROOT/apps/mp"
SCREENSHOT_DIR="$SCRIPT_DIR/screenshots"

GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { printf "${GREEN}[journey]${NC} %s\n" "$*"; }
warn() { printf "${YELLOW}[journey]${NC} %s\n" "$*"; }
err()  { printf "${RED}[journey]${NC} %s\n" "$*"; }

# ----- 参数解析 -----
RESTART_H5=0
CHECK_ONLY=0
for arg in "$@"; do
  case "$arg" in
    --restart-h5) RESTART_H5=1 ;;
    --check-only) CHECK_ONLY=1 ;;
    -h|--help)
      grep '^# ' "$0" | sed 's/^# //'
      exit 0 ;;
    *)
      err "未知参数: $arg"
      exit 1 ;;
  esac
done

mkdir -p "$SCREENSHOT_DIR"

# ----- Step 1: 依赖检查 -----
log "检查依赖..."
for cmd in uv pnpm node curl lsof; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    err "缺少依赖: $cmd"
    exit 1
  fi
done
log "依赖 OK: uv / pnpm / node / curl / lsof"

# ----- Step 2: 端口探测 + 起 API -----
api_alive() {
  curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:8000/api/v1/ipos/historical?limit=1" | grep -qE '^(200|404)$'
}

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  if api_alive; then
    log "API 已运行 (127.0.0.1:8000); 跳过启动"
  else
    log "API 未运行, 起 uvicorn..."
    (cd "$API_DIR" && nohup uv run uvicorn app.main:app --reload --port 8000 --host 127.0.0.1 \
       > /tmp/journey-api.log 2>&1 &)
    # 等就绪 (最多 30s)
    for i in $(seq 1 30); do
      sleep 1
      if api_alive; then
        log "API 起来了 (waited ${i}s)"
        break
      fi
      [[ $i -eq 30 ]] && { err "API 30s 起不来, 看 /tmp/journey-api.log"; exit 2; }
    done
  fi
fi

# ----- Step 3: H5 起 / 重启 -----
h5_alive() {
  curl -s -o /dev/null -w "%{http_code}" "http://localhost:5173/" | grep -q '^200$'
}

if [[ "$CHECK_ONLY" -eq 0 ]]; then
  if [[ "$RESTART_H5" -eq 1 ]] && lsof -ti :5173 >/dev/null 2>&1; then
    warn "强制重启 H5 (--restart-h5); 杀 5173 端口占用..."
    lsof -ti :5173 | xargs -r kill -9 || true
    sleep 2
  fi

  if h5_alive; then
    log "H5 已运行 (localhost:5173); 跳过启动"
    warn "若 pages.json 改过, 请重跑: ./run_journey.sh --restart-h5"
  else
    log "H5 未运行, 起 vite..."
    (cd "$MP_DIR" && nohup env UNI_INPUT_DIR=. pnpm dev:h5 \
       > /tmp/journey-h5.log 2>&1 &)
    for i in $(seq 1 30); do
      sleep 1
      if h5_alive; then
        log "H5 起来了 (waited ${i}s)"
        break
      fi
      [[ $i -eq 30 ]] && { err "H5 30s 起不来, 看 /tmp/journey-h5.log"; exit 3; }
    done
  fi
fi

# ----- Step 4: 自检 (路由 + 数据) -----
log "自检 1/3: API 历史 IPO 接口..."
RESP=$(curl -s "http://127.0.0.1:8000/api/v1/ipos/historical?limit=1")
if ! echo "$RESP" | grep -q '"items"'; then
  err "API /ipos/historical 返回异常:"
  echo "$RESP" | head -c 500
  exit 4
fi
log "  OK ($(echo "$RESP" | grep -oE '"total":[0-9]+' | head -1) 行回填数据)"

log "自检 2/3: API 行业聚合接口 (用 06922.HK 当锚点)..."
RESP_PA=$(curl -s "http://127.0.0.1:8000/api/v1/ipos/06922.HK/peer-aggregate")
if ! echo "$RESP_PA" | grep -qE '"(stats|insufficient)"'; then
  warn "  /peer-aggregate 返回异常 (可能 06922.HK 不在 DB), 继续"
else
  log "  OK"
fi

log "自检 3/3: H5 / pages 路由响应..."
ROUTES=("" "#/pages/ipo/historical" "#/pages/ipo/historical-pattern" "#/pages/me/index")
ALL_OK=1
for r in "${ROUTES[@]}"; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:5173/$r")
  if [[ "$CODE" == "200" ]]; then
    log "  OK  $r → $CODE"
  else
    err "  FAIL $r → $CODE"
    ALL_OK=0
  fi
done
[[ "$ALL_OK" -eq 0 ]] && exit 4

# ----- Step 5: 总结 -----
echo
log "========================================"
log "全部就绪. 现在用 cursor browser-use MCP 跑:"
log "  ${SCRIPT_DIR}/test_user_journey.md"
log ""
log "截图保存到: ${SCREENSHOT_DIR}/"
log "========================================"
