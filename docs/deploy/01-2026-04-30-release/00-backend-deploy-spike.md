# 00 — 后端部署 Spike (Backend Deploy)

> **目的**: 在小程序首次发版前, 把 FastAPI 后端**真实部署到公网**, 配 HTTPS, 让微信小程序的 ``request`` 合法域名能打通.
> **生成时间**: 2026-04-30 10:35
> **状态判断**: 当前 `apps/api/.env` 关键字段为空 + 早先 `curl https://api.xinguzh.com/healthz` DNS 解析失败 → **后端尚未部署到公网**, 这份文档是**真上线前必经路径**.
> **参考**: `docs/RUNBOOK.md` §部署方案 B 已有 ~150 行细节, 本 spike **不重复**, 只做 "决策 + 命令贴" 收口.

---

## TL;DR · 30 秒结论

> **推荐路径**: **腾讯云轻量上海 2C4G ¥48/月** + **单机 docker-compose** (PG / Redis / API) + **Caddy** (反代 + 自动 HTTPS) + **阿里云短信 + 硅基流动 LLM**.
>
> **总耗时**: **3-4 小时**(假设域名已备案 + 服务器已开 + SSH 通)
>
> **总成本**: **¥48/月起** (服务器) + **~¥45/月** (5K DAU 时 LLM) + **¥0.045/条** (短信, ~¥50/月内测期足够) ≈ **¥150/月**
>
> **关键点**: 不要为了"省"自建 RDS. 单机 ECS 跑 docker compose + 每日 pg_dump 是 DAU < 1000 的最佳性价比; DAU 上千再升 RDS, **现在不要预备性能**.

---

## §1. 当前状态 (5 分钟自检)

### 1.1 后端代码就绪度 ✅

| 项 | 状态 | 备注 |
|----|-----|-----|
| `apps/api/Dockerfile` | ✅ 已就绪 | python:3.12-slim + uv 安装链 + healthcheck (`/healthz`) |
| `apps/api/app/main.py` | ✅ 已就绪 | FastAPI lifespan + CORS + sentry init + scheduler + healthz endpoint |
| `apps/api/alembic/versions/` | ✅ head = `0015_ipos_price_range` | 比文档前面写的 0014_community 多一版 |
| `infra/docker-compose.yml` | ✅ 已就绪 | postgres (pgvector/pg16) + redis (7-alpine) + meilisearch (v1.10) |
| `apps/api/Makefile` | ✅ 已就绪 | `make ci-integration` 一键跑 ~1123 测试 |

### 1.2 .env 缺什么 (本地实测)

跑 `grep ... apps/api/.env`, 关键字段缺失情况:

| 字段 | 当前 | 上线必填? |
|-----|------|---------|
| `JWT_SECRET` | ❌ EMPTY | **必填** (32+ 字节随机串, 不填登录全炸) |
| `SILICONFLOW_API_KEY` 或 `DEEPSEEK_API_KEY` | ❌ 都 EMPTY | **必填**至少 1 个 (AI 全靠它) |
| `WECHAT_MP_APP_ID` / `WECHAT_MP_APP_SECRET` | ❌ EMPTY | **必填** (微信一键登录 + 小程序 code2Session) |
| `ALIYUN_SMS_*` | ❌ EMPTY | **必填**(生产 OTP, dev 走 mock 凑合, 上线必须真) |
| `OPS_ADMIN_TOKEN` | (检查) | **必填** (admin endpoint 全靠它鉴权) |
| `SENTRY_DSN` | (检查) | 强烈推荐 |
| `ALERT_DINGTALK_WEBHOOK` | (检查) | 强烈推荐 |
| `CORS_ORIGINS` | ✅ SET | 生产记得改成真域名 |

### 1.3 未部署的硬证据

| 证据 | 含义 |
|------|-----|
| `curl https://api.xinguzh.com/healthz` → DNS 解析失败 | 子域名 `api` 还没 A 记录 / 服务器还没起 |
| `apps/api/.env` 多个生产关键字段 EMPTY | 还在用本地 dev 配置 |
| `apps/mp/dist/` 只有 `dev/`, 没 `build/` | 还没出过提审包(刚构建没事, 我们 D2 出) |

**结论**: 后端 0 → 1, 必走完整部署. 不是"已部署只补几行配置"的小动作.

---

## §2. 三档部署方案对比 (选一档, 别中途换)

### 2.1 方案 X · 极简免费版 (¥0/月, 内测期专用)

> **适用**: 自己 + 5 个白名单内测用户, 完全没收入.

```
腾讯云轻量 [新人 1 元试用 6 个月] ¥1/6月
  ↓
docker compose: postgres + redis (单容器, 数据丢就丢)
  ↓
caddy 自动 HTTPS (Let's Encrypt 免费证书)
  ↓
LLM: 硅基流动新人送 ¥14 额度 (够 ~7 天)
SMS: 不用阿里云, 用 mock 通道 (发版前换)
```

**致命缺陷**: 试用期一过 / 数据丢了 / 短信不能给真用户发. **只用来跑通流程**, 不能上线.

### 2.2 方案 Y · 内测期实用版 (¥48-100/月) ⭐ **推荐这档**

> **适用**: 灰度发布 + 早期用户 100~500.

```
腾讯云轻量 SVS 2C4G 60GB SSD ¥48/月  (CN 上海, 备案最顺)
  ↓
docker compose: postgres-pgvector + redis (data 挂卷持久化)
            uvicorn workers=2 (gunicorn 不需要, uvicorn 直接撑)
  ↓
caddy 反代 + 自动 HTTPS + 限流 zone
  ↓
LLM: 硅基流动 ¥30 充值 (~30 天 100 DAU 够) → DeepSeek 备
SMS: 阿里云 ¥0.045/条 (备案 + 签名 + 模板, 1-3 天审批)
监控: Sentry Free + DingTalk 机器人 (¥0)
备份: cron 每日 pg_dump → COS / OSS ¥6/年 50GB
```

**总成本**: ~¥80/月 (含 LLM 浮动). 撑到 DAU 500 不用动.

### 2.3 方案 Z · DAU > 500 才考虑

> 现在不做. RDS PostgreSQL ¥150/月 + ECS 4C8G ¥200/月 + Sentry Pro ¥260/月. 总 **~¥800/月**. 等真撑不住单机再升, **现在升纯属浪费**.

---

## §3. 推荐路径详细落地 (方案 Y, 命令级)

> 假设你已有: 腾讯云账号 + 已购轻量服务器 + 已备案域名(主域名+子域名 `api.x` 解析到服务器 IP).
>
> 没有的话: 见 §6 "前置准备", 先去办这 3 件.

### 3.1 服务器初始化 (15 min)

SSH 上服务器 (例: `ssh ubuntu@<你的服务器 IP>`), 跑:

```bash
# === 1. 系统升级 + 必备工具 ===
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y curl ca-certificates git ufw

# === 2. Docker (官方一键脚本) ===
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker
docker --version  # 期望: Docker version 27+
docker compose version  # 期望: v2.x

# === 3. Caddy (反代 + 自动 HTTPS) ===
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update && sudo apt-get install -y caddy
caddy version  # 期望: v2.7+

# === 4. 防火墙 (UFW) ===
sudo ufw allow OpenSSH       # 22
sudo ufw allow 80/tcp         # caddy http (80 自动转 443)
sudo ufw allow 443/tcp        # caddy https
sudo ufw --force enable
sudo ufw status               # 期望: Status: active, 22/80/443 ALLOW

# === 5. (可选) 把 8000 端口锁死, 不让公网直接访问 API, 只走 caddy ===
sudo ufw deny 8000/tcp
sudo ufw deny 5432/tcp        # postgres 也只让本机访问
sudo ufw deny 6379/tcp        # redis 同
```

**腾讯云控制台还要开一次安全组**:
1. 控制台 → 轻量应用服务器 → 防火墙 → 添加规则
2. 开 22 (SSH) / 80 (HTTP) / 443 (HTTPS) → 来源 `0.0.0.0/0`
3. 不开 8000 / 5432 / 6379 (这些只让 docker 内部访问)

### 3.2 拉代码 + 起基础设施 (10 min)

```bash
# === 1. 拉代码 ===
cd /opt
sudo mkdir -p xgzh && sudo chown $USER:$USER xgzh
cd xgzh
git clone <你的 git 地址>.git .   # 例: git@github.com:youzi530/xgzh.git

# === 2. 起 PG + Redis (走 infra/docker-compose.yml) ===
cd infra
# Meilisearch 这次发版用不上, 不要起 (省 ~300MB 内存)
docker compose up -d postgres redis
docker compose ps                 # 期望: postgres + redis 都 healthy
docker compose logs postgres | tail -20  # 看一眼有没有 init 报错

# === 3. 修改 PG 默认密码 (生产必做!) ===
# infra/docker-compose.yml 里默认密码是 xgzh_dev_pass, 生产必须改
# 但已经起了实例的话, 改环境变量不会生效, 需要 ALTER USER:
docker compose exec postgres psql -U xgzh -d xgzh -c "ALTER USER xgzh WITH PASSWORD '$(openssl rand -hex 16)';"
# 把生成的密码存到 .env 的 DATABASE_URL 里 (下一步)
# 或者: 删卷重起 (内测期数据没价值时):
# docker compose down && rm -rf data/postgres/* && (改 docker-compose.yml POSTGRES_PASSWORD) && docker compose up -d
```

### 3.3 配置生产 .env (15 min, 最容易踩坑)

```bash
cd /opt/xgzh/apps/api
cp .env.example .env
nano .env   # 或 vim, 按你习惯
```

**必填字段** (其它保持 .env.example 默认即可):

```bash
# ==== 服务器 ====
APP_NAME=xgzh-api
APP_ENV=prod                                    # ← dev → prod
LOG_LEVEL=INFO                                  # 不要 DEBUG, 日志爆炸
CORS_ORIGINS=https://servicewechat.com,https://<你的域名>
                                                # ← servicewechat.com 是微信小程序 webview 的来源, 必加

# ==== LLM (必填至少一个) ====
SILICONFLOW_API_KEY=sk-xxxxxxxxxxxxxxxxxxx     # ← 去 siliconflow.cn 注册 + 充 ¥30
LLM_PRIMARY_MODEL=openai/deepseek-ai/DeepSeek-V3
LLM_FALLBACK_MODEL=openai/THUDM/glm-4-9b-chat   # ← 主炸了用备的, 默认就行

# ==== Redis ====
REDIS_URL=redis://redis:6379/0                  # ← 注意! docker compose 内部用 service name, 不是 localhost

# ==== Postgres (用上面 ALTER 后的真实密码) ====
DATABASE_URL=postgresql+asyncpg://xgzh:<生成的随机密码>@postgres:5432/xgzh
                                                # ← 同样, host 是 postgres 不是 localhost

# ==== JWT (生产必生成新的!) ====
JWT_SECRET=                                     # ← 跑: openssl rand -hex 32, 把输出粘这里

# ==== 微信小程序 ====
WECHAT_MP_APP_ID=wxe525868b30a43b96             # ← 已知
WECHAT_MP_APP_SECRET=                           # ← mp.weixin.qq.com → 开发管理 → 开发设置 → AppSecret 重置生成

# ==== 短信 OTP (生产必走阿里云) ====
SMS_ADAPTER=aliyun                              # ← 改 aliyun
ALIYUN_SMS_ACCESS_KEY_ID=                       # ← 阿里云 → RAM → 创建 AK (子账号, 仅 SMS 权限)
ALIYUN_SMS_ACCESS_KEY_SECRET=                   # 同上
ALIYUN_SMS_SIGN_NAME=新股智汇                   # ← 阿里云短信 → 国内消息 → 签名管理 申请
ALIYUN_SMS_TEMPLATE_ID=SMS_xxxxxxxxx           # ← 同上, 模板管理 申请

# ==== Admin endpoint 鉴权 ====
OPS_ADMIN_TOKEN=                                # ← 跑: openssl rand -hex 32, 同 JWT_SECRET 操作

# ==== 微信支付 (个人版长期 stub, 不动) ====
WECHATPAY_DEV_MODE=true                         # ← 个人版必须 true, 不要改

# ==== 监控 (可选但强烈推荐, 内测期 0 元也能用) ====
SENTRY_DSN=                                     # ← sentry.io 注册免费版 (每月 5K events 够内测), 创建 project 拿 DSN
SENTRY_ENVIRONMENT=prod
SENTRY_TRACES_SAMPLE_RATE=0.1
ALERT_DINGTALK_WEBHOOK=                         # ← 钉钉群 → 群机器人 → 自定义 → 加签 (推荐) 拿 webhook
ALERT_DINGTALK_SECRET=                          # 同上
ALERT_RUNBOOK_BASE_URL=https://github.com/<你>/xgzh/blob/main/docs/runbooks  # 可选
```

**配置完跑一次校验**:

```bash
# 在服务器上, 不进容器, 用本机 python 临时 import 一次 settings 看校验过不过:
docker compose -f /opt/xgzh/infra/docker-compose.yml run --rm \
  -v /opt/xgzh:/work -w /work/apps/api --env-file .env \
  python:3.12-slim python -c "
import os
required = ['JWT_SECRET', 'SILICONFLOW_API_KEY', 'WECHAT_MP_APP_ID', 'WECHAT_MP_APP_SECRET', 'ALIYUN_SMS_ACCESS_KEY_ID', 'OPS_ADMIN_TOKEN']
missing = [k for k in required if not os.getenv(k)]
print('MISSING:', missing) if missing else print('OK, all required env set')
"
# 期望: OK, all required env set
```

### 3.4 起 API 容器 (10 min)

把 `apps/api` 也用 docker compose 跑, 跟 PG/Redis 同 network:

```bash
cd /opt/xgzh/infra
nano docker-compose.yml  # 追加 api service
```

在 `services:` 下追加(注意缩进):

```yaml
  api:
    build:
      context: ../apps/api
      dockerfile: Dockerfile
    container_name: xgzh-api
    env_file:
      - ../apps/api/.env
    ports:
      - "127.0.0.1:8000:8000"     # 只绑 localhost, 不让公网直连
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "curl -fsS http://localhost:8000/healthz || exit 1"]
      interval: 30s
      timeout: 5s
      start_period: 30s
      retries: 3
    restart: unless-stopped
```

build + 起:

```bash
docker compose up -d --build api
docker compose logs -f api &       # 后台看日志
sleep 30
curl -fsSL http://127.0.0.1:8000/healthz  # 期望 {"status":"ok",...}
```

**第一次起最容易报错的 3 类**:

| 错 | 修法 |
|----|-----|
| `connection refused: postgres:5432` | `.env` 的 `DATABASE_URL` host 写成 `localhost` 了, 改成 `postgres` (docker service 名) |
| `JWT_SECRET must be set` | `.env` 没填或起容器时没指定 `--env-file` |
| `litellm.AuthenticationError` | LLM key 错或没充值, 检查 SiliconFlow / DeepSeek 控制台余额 |

### 3.5 跑 alembic 迁移 (5 min)

API 起来后, 跑数据库迁移:

```bash
docker compose exec api uv run alembic upgrade head
docker compose exec api uv run alembic current
# 期望: 0015_ipos_price_range (head)
```

种数据 (券商表 / 历史 IPO 合成数据):

```bash
docker compose exec api uv run python -m scripts.seed_brokers
docker compose exec api uv run python -m scripts.backfill_historical_ipos --source synthetic --target-rows 600
docker compose exec api uv run python -m scripts.check_historical_coverage  # 期望退出码 0
```

### 3.6 配 Caddy 反代 + HTTPS (10 min)

```bash
sudo nano /etc/caddy/Caddyfile
```

写入 (替换域名):

```caddy
api.<你的域名> {
    reverse_proxy 127.0.0.1:8000

    encode gzip

    # 简单日志
    log {
        output file /var/log/caddy/api.log
        format json
        level INFO
    }

    # 限流 (caddy 默认无内置, 用 mod_ratelimit 或交给 BE 自己限流)
    # BE 已有 @app/cache/RateLimitExceeded, caddy 这里不重复
}

# 主域名 (将来 H5 用; 本次发版可暂时只配 api 子域)
# <你的域名> {
#     root * /var/www/xgzh-h5
#     file_server
# }
```

启动 + 自动签证书:

```bash
sudo systemctl restart caddy
sudo systemctl enable caddy
sudo journalctl -u caddy -n 50    # 期望: certificate obtained successfully
sudo caddy validate --config /etc/caddy/Caddyfile  # 配置语法校验
```

外网验证:

```bash
# 在你 Mac 上跑:
curl -fsSL https://api.<你的域名>/healthz
# 期望: {"status":"ok","app":"xgzh-api","env":"prod","llm_configured":true,...}
```

**HTTPS 证书 1-2 分钟自动签发** (caddy 会找 Let's Encrypt). 如果 90 秒还没证书, 看 `journalctl -u caddy`, 大概率是:
- 80 端口被占了 (caddy 用 ACME-HTTP 验证): `sudo lsof -i :80` 看, kill 占用进程
- 域名 A 记录还没生效: `dig api.<你的域名>` 验证 IP
- 防火墙没开 80: `sudo ufw status`

### 3.7 配定时任务: 备份 + 健康检查 (10 min)

```bash
sudo nano /etc/cron.d/xgzh-ops
```

写入:

```cron
# 每天 03:00 备份 PG → 本地 (内测期手动 scp 走或换成 oss/cos cli)
0 3 * * * ubuntu /usr/bin/docker exec xgzh-postgres pg_dump -U xgzh xgzh | gzip > /opt/xgzh/backups/xgzh-$(date +\%Y\%m\%d).sql.gz

# 每 5 分钟健康检查 (curl 本机, 失败发 DingTalk)
*/5 * * * * ubuntu curl -fsS http://127.0.0.1:8000/healthz > /dev/null || \
  curl -X POST -H "Content-Type: application/json" \
       -d '{"msgtype":"text","text":{"content":"XGZH-ALERT 后端 healthz 失败 @ '$(date)'"}}' \
       <你的 DingTalk webhook>

# 每周日 04:00 清理 30 天前备份
0 4 * * 0 ubuntu find /opt/xgzh/backups/ -name "xgzh-*.sql.gz" -mtime +30 -delete
```

```bash
mkdir -p /opt/xgzh/backups
sudo systemctl restart cron
```

---

## §4. 全链路验证 (上小程序前必跑一遍, 15 min)

### 4.1 后端自身

```bash
# 1. healthz (公网)
curl -fsSL https://api.<你的域名>/healthz | jq .
# 期望:
# {
#   "status": "ok",
#   "app": "xgzh-api",
#   "env": "prod",
#   "llm_configured": true,
#   ...
# }

# 2. 接口可达性 (无需 token 的)
curl -fsSL https://api.<你的域名>/api/v1/ipos?limit=5 | jq '.items | length'
# 期望: 5 (synthetic 已种)

# 3. admin dashboard
curl -fsSL -H "X-Admin-Token: <你的 OPS_ADMIN_TOKEN>" \
  https://api.<你的域名>/api/v1/admin/dashboard?days=1&format=json | jq .
# 期望: 一坨指标 JSON, 不是 401

# 4. SMS 链路 (mock 校验; 真发短信会扣钱, 跳过 / 先用一次性号码)
# 略, 先打通 OTP 流程后再压测真发

# 5. LLM 链路 (低成本调一次)
curl -fsSL -X POST https://api.<你的域名>/api/v1/agent/chat-diagnose \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"什么是 IPO 申购?"}],"ipo_code":null}' \
  --max-time 30
# 期望: SSE 流式返回, 看到 data: {"event":"start",...} ... data: {"event":"complete",...}
```

### 4.2 微信小程序后台同步

mp.weixin.qq.com → 开发管理 → 开发设置 → 服务器域名:

```
request 合法域名:  https://api.<你的域名>
uploadFile / downloadFile / socket: 留空 (本次不用)
```

加完保存, 等 3 分钟生效. 没加的话提审第一步就被打回.

### 4.3 体验版扫码冒烟

```bash
# 本地出包
cd /Users/youzi530/lingqiao/demand-engine-team/xgzh/apps/mp
rm -rf dist/build && pnpm build:mp-weixin
# 微信开发者工具打开 dist/build/mp-weixin/ → 上传 1.0.0 → 选为体验版
# 用真手机扫体验版码, 跑前面的 5 步 P0
```

5 步全过 → 后端真打通了. 进上一步给的小程序提审流程.

---

## §5. 常见坑 + 速查 (8 个)

### 坑 1: caddy 拿不到证书

**症状**: `journalctl -u caddy` 看到 `acme: error: ... timeout` 或 `connection refused`.

**修法**:
1. 验证 80 端口能从公网访问: 在你 Mac 上 `curl http://api.<你的域名>` (不是 https), 期望 redirect to https 而非 timeout.
2. 80 端口被占: `sudo lsof -i :80` → kill (例: nginx)
3. 域名 A 记录没生效: `dig +short api.<你的域名>` 应该返回服务器公网 IP. 没生效等 5 分钟 (DNS 传播)
4. 备案在审或失效: 国内服务器**没备案不能开 80**, 阿里云 / 腾讯云会主动拦截

### 坑 2: 容器内连 postgres 失败

**症状**: API 容器日志 `connection to server at "localhost", port 5432 failed`

**修法**: `.env` 的 `DATABASE_URL` host 必须是 docker compose service 名 (`postgres`) 而非 `localhost`. localhost 在容器内指容器自己, 不是宿主.

### 坑 3: alembic 迁移卡死

**症状**: `alembic upgrade head` 输出一会就停住.

**修法**: 大概率是某个迁移在等锁:
```bash
docker compose exec postgres psql -U xgzh -d xgzh -c \
  "SELECT pid, query, state FROM pg_stat_activity WHERE state != 'idle';"
# 找到长跑的, kill: SELECT pg_terminate_backend(<pid>);
```

### 坑 4: SiliconFlow API key 报 401

**症状**: AI 接口返 502, 后端日志 `litellm.AuthenticationError`.

**修法**:
- 控制台余额 0 → 充 ¥30
- key 复制时多了空格 → 重新复制粘贴
- key 锁定了 (新人福利 key 经常失效) → 控制台重新生成

### 坑 5: 阿里云短信签名审批不过

**症状**: 阿里云 → 短信服务 → 签名审批被驳回 "签名与小程序主体不符".

**修法**:
- **个人版**: 签名只能用本人姓名相关的, 不能用品牌名 "新股智汇"
  - 改成 "<你的真实姓名>" + 模板 "您的验证码是 XXXX, 用于注册新股智汇" — 个人版常见做法
  - 或者**直接不接阿里云**, 内测期就用 dev 的 mock OTP, 提审备注写 "测试账号 mock 通道"
- **企业版**: 签名必须跟营业执照主体一致

### 坑 6: 内存爆 (轻量 4G 配置, docker compose 三件套吃满)

**症状**: `docker stats` 看到 postgres / redis / api 三个容器一起吃 3.5G+, 偶尔 OOM kill.

**修法**:
- meilisearch 不要起 (省 ~300MB)
- redis maxmemory 从 512MB 降到 256MB (`infra/docker-compose.yml`)
- postgres `shared_buffers=128MB` 限制 (写到 init/postgres/postgresql.conf)
- 实在不够 → 升 6G ¥80/月

### 坑 7: 备案期间不能开 80/443

**症状**: 备案在审, 服务器 80/443 被腾讯 / 阿里云封.

**修法**: 等. 备案下来才能开. 这期间走 §2.1 方案 X 的 cloudflare tunnel 临时走 (但小程序提审需要正经域名, 没办法绕开备案).

### 坑 8: gunicorn vs uvicorn workers

> RUNBOOK 说 "生产用 gunicorn -w 4 -k uvicorn.workers.UvicornWorker". 本 spike 直接用 docker 容器内 uvicorn 单进程, 因为:
> 1. 单容器 + workers > 1 时 APScheduler 会跑 4 次同一个 cron (每个 worker 都起一次), 数据双写
> 2. 内测期 100 DAU 单进程够撑
> 3. 真要 multi-worker 时, 上 k8s 把 scheduler 拆成独立 deployment + workers 跑 API + scheduler 单实例

**症状**: APScheduler 一次任务跑 4 次, DB 重复入库.

**修法**: docker compose 单容器单进程, 不要 -w 4. 或者:
```python
# app/scheduler/__init__.py 加锁:
SCHEDULER_LEADER_REDIS_KEY = "xgzh:scheduler:leader"
# 启动时 setnx with TTL, 拿到锁的 worker 才跑 scheduler, 其它 worker 跳过
# 这是后续 sprint 的活, 现在单容器单进程绕过
```

---

## §6. 前置准备 (没办的话先去办)

> 这部分**不在本 spike 时间预算内**, 因为有审批等待.

### 6.1 服务器

| 选项 | 厂家 | 价格 | 时长 | 推荐 |
|------|-----|-----|-----|------|
| 腾讯云轻量上海 SVS 2C4G | tencent | ¥48/月 | 即时 | ⭐ 推荐, CN 节点合规 + 微信支付亲和 |
| 阿里云 ECS 突发 t6 2C4G | alibaba | ¥60/月 | 即时 | 备选, 政策稳定 |
| 字节火山引擎 2C4G | bytedance | ¥40/月 | 即时 | 价格略低, 但生态弱 |
| 海外: 阿里云香港轻量 2C2G | alibaba | $40/月 | 即时 | **不需要备案**, 但 CN 用户延迟 100ms+ |

### 6.2 域名 + 备案

| 任务 | 时长 | 注意 |
|------|-----|-----|
| 域名 (`.com`) 注册 | 即时 | 阿里云 / 腾讯云 ¥69/年, 别在 godaddy (国内备案厂商一致) |
| ICP 备案 | **2-4 周** | 个人主体: 身份证 + 域名实名 + 服务器在国内 → 阿里 / 腾讯免费协助; 备案期间网站不可访问 |
| 备案号上墙 | 即时 | 备案下来后, 在小程序底部 / 网页 footer 加备案号显示 (合规要求) |

> **如果 ICP 还没下来** → 走 §2.1 方案 X 临时跑, 但**不能提审**(微信审核必须备案号一致). 老老实实等.

### 6.3 阿里云短信

| 任务 | 时长 | 注意 |
|------|-----|-----|
| 阿里云账号 + 实名 | 即时 | 已有跳过 |
| RAM 子账号 + AccessKey | 即时 | 仅授 `AliyunDysmsFullAccess` 权限, 不要 root key |
| 短信签名 | **1-3 天** | 个人版用本人姓名, 企业版用品牌名 |
| 短信模板 | **1-3 天** | "您的验证码 ${code}, 用于注册${app_name}, 5 分钟内有效." |

### 6.4 LLM key

| 选项 | 价格 | 注册 | 推荐 |
|------|-----|-----|------|
| **硅基流动** | DeepSeek-V3 ¥0.04/1Kt | siliconflow.cn 新人送 ¥14 | ⭐ 推荐, 一站式接入 DeepSeek + GLM + Qwen + bge |
| **DeepSeek 直连** | 同价 | platform.deepseek.com | 备份 |
| **智谱 GLM** | GLM-4-Flash 免费 | open.bigmodel.cn | fallback 用 |

### 6.5 微信公众平台

| 任务 | 时长 | 注意 |
|------|-----|-----|
| AppSecret 重置 | 即时 | mp.weixin.qq.com → 开发设置 → AppSecret → 重置(只显示一次, 立即存好) |
| 服务器域名 加 `https://api.<你的域名>` | 即时, 3min 生效 | 备案完成才能加, 备案前会被微信拦 |
| 业务域名 (web-view) | 跳过 | 本次不用 |

---

## §7. 上线后的日常运维 (vibe coding 单人版)

### 7.1 改代码上线

```bash
# 本地 push
git push origin main

# 服务器 pull + 重起 API
ssh ubuntu@<服务器>
cd /opt/xgzh
git pull origin main
cd infra
docker compose up -d --build api    # 只重 build api, PG/Redis 不动
docker compose logs -f api &
sleep 10
curl -fsSL http://127.0.0.1:8000/healthz
```

**5 分钟一个版本**. 第二次发版起搞 GitHub Actions 自动化 (后续 spike, 现在不做).

### 7.2 看日志

```bash
docker compose logs -f api --tail 100         # 实时 API 日志
docker compose logs postgres --tail 50        # PG 日志 (慢查询都在)
sudo tail -f /var/log/caddy/api.log           # caddy 反代日志 (5xx / 4xx 占比)
```

### 7.3 紧急回滚

```bash
ssh ubuntu@<服务器>
cd /opt/xgzh
git log --oneline -10                         # 找上一个稳定 commit
git checkout <稳定 sha>
cd infra
docker compose up -d --build api
sleep 10
curl -fsSL http://127.0.0.1:8000/healthz
```

**5 分钟内回滚到上版**. alembic schema 不要 downgrade (会丢数据), 旧 BE 读新 schema 大部分情况兼容.

### 7.4 数据库手动备份(发版前 / 大动作前)

```bash
ssh ubuntu@<服务器>
docker exec xgzh-postgres pg_dump -U xgzh xgzh | gzip > /opt/xgzh/backups/manual-$(date +%Y%m%d-%H%M).sql.gz
ls -lh /opt/xgzh/backups/                     # 验证生成
# scp 到本机或 OSS:
scp ubuntu@<服务器>:/opt/xgzh/backups/manual-*.sql.gz ~/Desktop/
```

### 7.5 监控看板入口收藏

| 用途 | URL / 命令 |
|------|---------|
| 后端 dashboard | `https://api.<域名>/api/v1/admin/dashboard?days=1&format=html` (要 X-Admin-Token header) |
| Sentry issues | `https://sentry.io/organizations/<你>/issues/?environment=prod` |
| DingTalk 告警群 | (微信群也行, 钉钉机器人 webhook 任一群) |
| 服务器 docker stats | `ssh ubuntu@... docker stats` |
| 阿里云短信用量 | 阿里云控制台 → 短信服务 → 用量统计 |
| 硅基流动 LLM 用量 | siliconflow.cn → 控制台 → 用量统计 |

---

## §8. 时间预算 (操作员实际耗时)

| 阶段 | 耗时 | 卡点 |
|------|-----|-----|
| 服务器初始化 (§3.1) | 15 min | 安装速度看网, ECS 国内 < 10 min |
| 拉代码 + 起 PG/Redis (§3.2) | 10 min | 拉代码看 git 速度 |
| 配 .env (§3.3) | 15 min | 找 6 个 key (LLM / 微信 / 短信 / Sentry / DingTalk), 真正卡在阿里云短信签名审批(已办则瞬间)|
| 起 API + 迁移 (§3.4-3.5) | 15 min | docker build 第一次 ~5min, 后续增量 < 30s |
| Caddy + HTTPS (§3.6) | 10 min | 等证书 1-2 min |
| Cron 备份 (§3.7) | 10 min | — |
| **小计 主动操作** | **75 min** | — |
| 全链路验证 (§4) | 15 min | — |
| **总计** | **~90 min** (1.5 小时) | — |

> 实际首次部署 95% 时间是在调环境变量 + 等证书. 真敲命令 < 30 min.

---

## §9. 决策日志

| 决策 | 选项 | 理由 |
|------|-----|------|
| 服务器 | 腾讯云轻量上海 ¥48/月 | CN 节点 + 备案最顺 + 微信支付亲和(虽然个人版不开支付, 未来升企业平滑) |
| DB | 自建 docker pgvector | DAU < 1000 性价比最高, 升 RDS 是 DAU 上 1000 后的事 |
| HTTPS | Caddy auto-cert | 0 配置 + 自动续期 + Let's Encrypt 免费 |
| API 部署 | docker compose 单进程 | 内测期足够 + APScheduler 不重复跑 |
| 监控 | Sentry Free + DingTalk webhook | 免费额度对内测足够 |
| LLM | 硅基流动主, GLM-4-Flash 备 | 价格 + 速度 + 国内合规 |
| SMS | 阿里云 (生产) / mock (备审) | 阿里云签名个人版只能用本人姓名, 提审用 mock 账号 (`13800138000` / `666666`) 就够 |
| 微信支付 | 长期 stub | 个人版不能开 |

---

## §10. 一行总结

> **腾讯云轻量 + docker compose + Caddy + 阿里云短信 + 硅基流动 = 1.5 小时部署完, ¥150/月跑得稳, 撑到 DAU 500 不用动.**

下一步 → 跑完本 spike § 3 + § 4, 后端在公网真打通, 然后回去走小程序快速上线 6 步流程 (build → 上传 → 体验版 → 提审 → 等 → 发布).

---

> 🎯 **vibe coding 收尾**: 本 spike 假设你**已有**腾讯云账号 / 备案域名 / 阿里云账号. 没办的话 §6 三件**先去办**(2-4 周等备案是硬伤), 期间可以走 §2.1 方案 X 在本地 + cloudflare tunnel 临时跑通流程, 等备案下来再切真服务器. 不要为了赶节奏跳过备案, 提审一定挂.
