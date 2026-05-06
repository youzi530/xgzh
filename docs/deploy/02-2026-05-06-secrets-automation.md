# 02 — 2026-05-06 .env 自动化重构

> **背景**: Sprint 9 BUG-S9-001 / S9-002 加了 `WECHAT_MP_APP_SECRET`、`AVATAR_PUBLIC_BASE_URL` 等
> 5 个新环境变量. 历史 deploy 流程是 **加新字段就要 SSH 上服务器手改 `.env`** — 容易漏、
> 容易错、追责难. 本次把这件事**全自动化**, 之后加配置只改文件即可.

> **决策人**: youzi530@outlook.com  ·  **改动文件**: 2 个 (`docker-compose.production.yml`,
> `.github/workflows/deploy.yml`)  ·  **一次性人工**: 1 件 (上 GH UI 配 secrets, ≤ 15 min).

---

## TL;DR

| 你以前要做什么 | 现在改成 |
|---|---|
| 每加 1 个 env 字段, SSH 上服务器 `vim .env` 加 3 行 | **加非 secret**: 改 `infra/docker-compose.production.yml` push → 自动生效 |
| | **加 secret**: 改 `.github/workflows/deploy.yml` 的 envs 列表 + GH UI 加 secret → 下次 push 自动生效 |
| `.env` 是不可见的事实来源, 文件挂在哪里 / 内容啥样要 SSH 才知道 | `.env` 由 `deploy.yml` 每次 push 自动重写, 内容**等价于 GH Secrets + 文件头注释** |
| 想临时调一个值, 改 `.env` 后下次 deploy 被覆盖 | 在 `/opt/xgzh/.env.local` 写覆盖, 不进 git, deploy 时**追加**到 `.env` 末尾 (优先级最高) |

---

## 1. 新架构: 配置事实来源 3 层模型

```text
┌──────────────────────────────────────────────────────────────────────────┐
│ 1. docker-compose.production.yml `environment` 块  (公开配置, 进 git)    │
│    - 70+ 个数值/开关/URL/默认 model slug 等                                │
│    - 改动: 编辑文件 → push → GH Actions auto deploy                      │
│    - 进容器的最终值 = environment 块展开 (优先级最高)                      │
├──────────────────────────────────────────────────────────────────────────┤
│ 2. /opt/xgzh/.env  (secrets, 服务器本地, 自动同步, 不进 git)              │
│    - 14 个真 secret + IMAGE_TAG (镜像 tag) + ACR_* 三件套                 │
│    - 由 deploy.yml 的 "Sync .env from GitHub Secrets" step 自动重写       │
│    - 内容来源 = GitHub Secrets (上面 settings 改, 下次 push 同步)         │
│    - chmod 600, 服务器其它账号读不到                                      │
├──────────────────────────────────────────────────────────────────────────┤
│ 3. /opt/xgzh/.env.local  (应急覆盖, 服务器本地, 不进 git, 不被自动重写)   │
│    - GH 不可用 / 紧急热修 / 临时调参时用                                   │
│    - deploy.yml 重写 .env 后会 cat .env.local >> .env, 同名 key 覆盖前者  │
│    - 用完想清理, 直接 rm /opt/xgzh/.env.local                             │
└──────────────────────────────────────────────────────────────────────────┘
        │
        ▼ docker compose 启动时
        ▼ 1. 自动 source .env (含 secrets + IMAGE_TAG)
        ▼ 2. environment 块的 ${VAR:-default} 引用 .env 的值
        ▼ 3. 容器内的 ENV = environment 块展开后的最终值
```

**优先级 (高 → 低)**: `.env.local` > `.env` (GH Secrets 同步) > `docker-compose.production.yml` 默认值

---

## 2. 一次性人工 SOP (≤ 15 min, 只做一次)

### 2.1 上 GitHub UI 配 secrets

去仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.

**必填 (5 个, 不配 deploy 失败)**:

| Secret 名字 | 值的来源 | 备注 |
|---|---|---|
| `POSTGRES_PASSWORD` | 现服务器 `/opt/xgzh/.env` 里 grep `POSTGRES_PASSWORD=` 那行 | PG 数据库密码, 改了要同步改 PG 容器 (推荐先不改) |
| `JWT_SECRET` | `openssl rand -hex 32` 生成 32 字节随机串 | 改了所有现存 token 全部失效, 用户要重新登录 |
| `DEPLOY_HOST` | 阿里云 ECS 公网 IP (如 `8.130.156.2`) | 已配过, 检查在不在 |
| `DEPLOY_USER` | SSH 用户名 (如 `root` 或 `ubuntu`) | 已配过, 检查在不在 |
| `DEPLOY_SSH_KEY` | `~/.ssh/id_rsa` 私钥**全文** (含 BEGIN/END 行) | 已配过, 检查在不在 |
| `ACR_REGISTRY` / `ACR_NAMESPACE` / `ACR_REPOSITORY` / `ACR_USERNAME` / `ACR_PASSWORD` | 阿里云容器镜像服务 | 已配过, 检查在不在 |

**Sprint 9 新增 (按需配, 留空 → 对应功能 503 不挂)**:

| Secret 名字 | 何时必填 | 来源 |
|---|---|---|
| `WECHAT_MP_APP_ID` | 想让小程序登录 / 用户头像跑通 | 微信公众平台 → 开发管理 → 开发设置 → AppID |
| `WECHAT_MP_APP_SECRET` | 同上 | 微信公众平台 → 开发管理 → 开发设置 → AppSecret (只显示 1 次, 务必保存) |
| `AVATAR_PUBLIC_BASE_URL` | 想让头像 URL 在小程序里显示 | 留空时默认 `http://8.130.156.2:8000/static/avatars`; 上 HTTPS 域名后改成 `https://api.xgzh.top/static/avatars` |
| `CORS_ORIGINS` | 加新前端来源 (H5 域名 / 小程序 webview 等) | 默认 `http://localhost:5173,https://api.xgzh.top,https://www.xgzh.top` |

**已有但建议同步 (≈10 个, dev 用过的复制过去)**:

| Secret 名字 | 当前是否在用 | 备注 |
|---|---|---|
| `ZHIPU_API_KEY` | ✅ 在用 | 主力 LLM (zhipu/glm-4-flash 免费) |
| `SILICONFLOW_API_KEY` | 备用 | 留空时跳过 |
| `DEEPSEEK_API_KEY` | 备用 | 留空时跳过 |
| `TUSHARE_TOKEN` | 备用 | A 股 IPO 数据源, 留空走 AKShare |
| `ALIYUN_SMS_*` (5 个) | 公司资质未拿到 | 全空, `SMS_ADAPTER` 默认 `mock` 不真发 |
| `SMS_ADAPTER` | `mock` (default) | 拿到资质后改 `aliyun` |
| `LONGBRIDGE_API_TOKEN` | Sprint 7.3 暂未启用 | 留空 |
| `OPS_ADMIN_TOKEN` | 想用 `/admin/*` endpoint | 留空时全 503 (默认状态, 安全) |
| `ALERT_DINGTALK_WEBHOOK` / `ALERT_DINGTALK_SECRET` | 想要钉钉错误告警 | 留空时仅 logger.error |
| `SENTRY_DSN` | 想要 Sentry error tracking | 留空时不上报 |
| `WECHATPAY_*` (6 个) | 想接真实微信支付 | `WECHATPAY_DEV_MODE=true` (default) 时其余可空, 走 Stub |

> **小贴士**: 暂时不知道值就别配, deploy.yml 会用空字符串写进 .env, BE 启动时该功能进 503 但其它功能不受影响.
> `POSTGRES_PASSWORD` + `JWT_SECRET` 是必填, 缺了 deploy step 主动 `exit 1` 阻断.

### 2.2 一次性把现服务器 .env 的 secrets 倒导给 GH UI

如果服务器 `/opt/xgzh/.env` 已经有值了, 不想从头生成, 用这条命令一次性导出 (注意只在自己能看到的本地终端跑):

```bash
ssh root@8.130.156.2 'cat /opt/xgzh/.env' | grep -E '^(POSTGRES_PASSWORD|JWT_SECRET|WECHAT_MP_APP_ID|WECHAT_MP_APP_SECRET|ZHIPU_API_KEY|SILICONFLOW_API_KEY|DEEPSEEK_API_KEY|TUSHARE_TOKEN|ALIYUN_SMS_|SMS_ADAPTER|LONGBRIDGE_API_TOKEN|OPS_ADMIN_TOKEN|ALERT_DINGTALK_|SENTRY_DSN|WECHATPAY_|AVATAR_PUBLIC_BASE_URL|CORS_ORIGINS)='
```

把输出每一行的 `KEY=VALUE` 拆开, 复制 `VALUE` 到 GH UI 对应 secret 名字.

---

## 3. 日常工作流 (重点!)

### 3.1 加 / 改一个**非 secret** 配置 (90% 场景)

```bash
# 例子: 把生产 LOG_LEVEL 从 INFO 改 DEBUG (临时排查问题)

# ✅ 一步: 改 docker-compose.production.yml, 找到 LOG_LEVEL 那行改默认值
git diff infra/docker-compose.production.yml
git add infra/docker-compose.production.yml
git commit -m "ops: bump prod LOG_LEVEL to DEBUG for issue #xxx"
git push origin main

# 之后 GH Actions 会自动:
#   1. 触发 deploy.yml
#   2. SSH 上服务器, 重写 /opt/xgzh/.env (secrets 部分)
#   3. docker compose up -d xgzh-api (滚动重启 BE, 加载新 environment 块)
#   4. 30s 内健康检查通过 → 排查完想恢复, 反向再 commit 一次
```

### 3.2 加 / 改一个**新 secret** (3% 场景, 例如新加阿里云 RAM key)

```bash
# 例子: 加一个 OSS_ACCESS_KEY_ID + OSS_ACCESS_KEY_SECRET

# 1. 改 .github/workflows/deploy.yml 的 "Sync .env from GitHub Secrets" step:
#    - env: 块加 OSS_ACCESS_KEY_ID + OSS_ACCESS_KEY_SECRET
#    - envs: 列表加 OSS_ACCESS_KEY_ID,OSS_ACCESS_KEY_SECRET
#    - heredoc 加 OSS_ACCESS_KEY_ID=$OSS_ACCESS_KEY_ID 两行
# 2. 改 infra/docker-compose.production.yml 加引用:
#    OSS_ACCESS_KEY_ID: ${OSS_ACCESS_KEY_ID:-}
#    OSS_ACCESS_KEY_SECRET: ${OSS_ACCESS_KEY_SECRET:-}
# 3. GH UI: Settings → Secrets → 加两个新 secret
# 4. git commit + push
```

### 3.3 紧急热修 / GH Actions 挂了 (≤ 1% 场景, **应急专用**)

```bash
# 例子: 凌晨发现某 LLM key quota 超了, 想立即换备用 key, 但不想等 GH Actions 跑 5min

ssh root@8.130.156.2
cd /opt/xgzh
# 写 .env.local (它会被 deploy 时追加到 .env 末尾, 同名 key 覆盖前者)
cat >> .env.local <<EOF
ZHIPU_API_KEY=新临时 key 值
EOF

# 立即重启 BE 容器加载
docker compose -f docker-compose.production.yml up -d xgzh-api

# 验证
curl http://localhost:8000/healthz

# ⚠ 重要: 后续记得做两件事
#   1. GH UI 同步把 ZHIPU_API_KEY 改成新值 (不然下次 deploy 会被旧 key 覆盖)
#   2. rm /opt/xgzh/.env.local (清理应急口子, 防遗忘)
```

### 3.4 怎么知道当前服务器跑的是啥配置?

```bash
ssh root@8.130.156.2
# 看 secrets (来自 .env)
sudo cat /opt/xgzh/.env

# 看 .env.local 应急覆盖 (一般空, 有就要警觉)
ls -la /opt/xgzh/.env.local 2>/dev/null && cat /opt/xgzh/.env.local

# 看公开配置 (容器内最终生效值, 经过 environment 块展开)
docker exec xgzh-api env | grep -E '^(LOG_LEVEL|CORS_ORIGINS|WECHAT_MP|AVATAR_|JWT_|RAG_)' | sort
```

---

## 4. 安全说明 (要看!)

1. **secrets 通过 SSH 传输, 全程 TLS 加密**, 不会出现在 GH Actions log 里 (GH 自动 mask).
2. **服务器 `/opt/xgzh/.env` 是 `chmod 600`**, 只有 root / 部署用户能读. 不要 `chmod 644`.
3. **绝对不要把 `.env`、`.env.local`、`.env.bak` 加到 git**. 已在 `.gitignore` 里.
4. **GH Secrets 一旦泄露立即换** (尤其 `JWT_SECRET` / `POSTGRES_PASSWORD` / `WECHAT_MP_APP_SECRET`):
   - GH UI 改 secret → push 任意 commit → deploy.yml 自动同步.
   - 改 `JWT_SECRET` 后所有 access token / refresh token 失效, 用户被踢登录.
5. **公开配置不是 secret, 但 `OPS_ADMIN_TOKEN` 例外** — 这是 admin endpoint 的 X-Admin-Token,
   被泄露等于 admin API 大开门, 必须当 secret 管.
6. **微信小程序 AppSecret 一旦泄露**, 第三方能拿来调微信开放 API (比如群发消息), 微信也只显示一次,
   泄露后必须去 mp 后台 reset 旧 secret.

---

## 5. 历史踩坑速记 (避免下次重复)

### 坑 1: pydantic-settings 字段名不带前缀

历史 spec/25 v1 写成 `XGZH_DATABASE_URL=...`, BE 用 pydantic-settings 默认 (no env_prefix), 字段名是
`DATABASE_URL`, 拿不到值, fallback 到 default `localhost:5432`, PG 容器 hostname `postgres` 没解析,
连不上. **但 `/healthz` 不查 DB 仍 healthy**, 误导排查. v2 已经修正: 写 `DATABASE_URL=...`.

### 坑 2: alembic upgrade head 没自动跑, 线上炸过

之前 `deploy.yml` 没自动跑 migration, 加了新字段的代码起来后, 调 `/auth/login/wechat-mp` 就 5xx
(因为 `users.wechat_openid` 列不存在). `/healthz` 不查 users 表, 健康检查不能抓, 自动 rollback 跑不到,
告警靠 Sentry. Sprint 9 已加 `docker compose run --rm xgzh-api alembic upgrade head`, deploy.yml
里幂等执行, 失败立即 `exit 1` 阻断后续 step.

### 坑 3: heredoc 里 secret 含 `$` 会被 shell 解释

如果 GH Secret 的值里有 `$xxx` 字面量 (例如某些 API key 长这样), `cat > .env <<EOF` (无引号) 会
把它当 shell 变量替换为空. **当前所有 secret 都是 hex/base64/URL token, 不含 `$`/反引号/`\`**, 安全.
未来如果加了奇怪格式的 secret, 改 heredoc 为 `<<'EOF'` (带引号) 关掉变量替换 — 但这样 `$ACR_REGISTRY`
等也不会展开了, 要把所有 `$VAR` 替换为另一种语法 (比如先 `printenv > .env` 再 grep -E 选取).

### 坑 4: docker compose 嵌套 default 不支持

`${WECHATPAY_APP_ID:-${WECHAT_MP_APP_ID:-}}` 这种嵌套语法 docker compose 不识别, 会保留 `:-` 后整段
当字面量. 已改成 `${WECHATPAY_APP_ID:-}` 单层, 默认空字符串.

---

## 6. 这次改了什么?

| 文件 | 改动 |
|---|---|
| `infra/docker-compose.production.yml` | environment 块从 ~30 行扩到 ~180 行, 把所有非 secret 配置都写进去, 配 default 值; 改了头注释解释新规约 |
| `.github/workflows/deploy.yml` | 加了一个新 step `Sync .env from GitHub Secrets` (在 build/push 之后, deploy 之前); 重写了头注释 |
| `docs/deploy/02-2026-05-06-secrets-automation.md` (本文件) | 新建迁移文档 |

---

## 7. 验收 checklist

部署后按这个清单走一遍, 全打钩才算自动化生效:

- [ ] GH Actions 跑完 deploy workflow, 看 "Sync .env from GitHub Secrets" step 输出 `✅ /opt/xgzh/.env synced`
- [ ] SSH 到服务器: `cat /opt/xgzh/.env` 能看到所有 secret 都有值 (或显式空字符串)
- [ ] `stat -c %a /opt/xgzh/.env` 输出 `600`
- [ ] `docker exec xgzh-api env | grep WECHAT_MP_APP_ID` 能看到值 (确认 environment 块加载成功)
- [ ] `curl http://8.130.156.2:8000/healthz` 返回 `200 OK`
- [ ] 用真机微信扫小程序测试码, 能成功微信登录 (验证 `WECHAT_MP_APP_SECRET` 注入到容器)
- [ ] (可选) 改 `infra/docker-compose.production.yml` 一个无害值 (如 `LOG_LEVEL: DEBUG`), commit + push, 5 min 内 GH Actions 跑完, 服务器容器内 LOG_LEVEL 应该变成 DEBUG (`docker exec xgzh-api env | grep LOG_LEVEL`)
- [ ] 手动改服务器 `.env` 一个值, 下次 push 后该改动**应该被覆盖** (验证 .env 真的是 GH 的 mirror)

---

## 8. 与现有文档的关系

- 本文件 = `docs/deploy/02-...` (Sprint 9 配置自动化, 2026-05-06)
- `docs/deploy/01-2026-04-30-release/` = 首次正式发版工作包 (2026-04-30)
- 后续每个 release / 大改基础设施都新建 `docs/deploy/0N-YYYY-MM-DD-*/`, 保留历史.

`spec/25-deployment-strategy.md` 是顶层策略文档, 本文件是它的执行细节. 等下次大重构时把本文件的关键决策
反向同步到 spec/25.
