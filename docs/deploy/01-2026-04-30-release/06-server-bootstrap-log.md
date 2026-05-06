# 06 — 服务器部署实战复盘 (2026-04-30)

> **目的**: 把今天**从买服务器到 `/healthz` 200**的完整动作流水账记下来, 方便:
> 1. 下次重新部署 / 灾后重建时**复制粘贴**, 不用再踩一遍 5 小时的坑
> 2. 团队后来人 onboard 时按这个 SOP 走, 不要重新 spike
> 3. **真出事回滚**时知道每个组件的来龙去脉
>
> **不是**[`00-backend-deploy-spike.md`](./00-backend-deploy-spike.md) 的重复 ─ 那份是 *"打算怎么做"* 的方案; 这份是 *"实际怎么做的, 踩过哪些坑, 最后怎么解决"* 的记录.
>
> **生成时间**: 2026-04-30 16:00 (后端容器栈 healthy 后 30 分钟现写)

---

## TL;DR · 30 秒结论

| 项 | 数字 |
|---|---|
| 总耗时 (server 开机 → /healthz 200) | **~3 小时 20 分钟** (12:10 → 15:31) |
| 真错误踩坑数 | **3 个** (apt 慢 + README 缺 + uv run 重 sync) |
| 不动手的等待时间 | **~2.5 小时** (apt 下载 + uv 包下载) |
| 真敲键盘时间 | **~30-40 分钟** |
| 最终成本 | 服务器 ¥ ~700/年 + 域名 ¥ ~20/年 + LLM **¥ 0 (Zhipu 免费)** |

**血的教训**: **不要在 3 Mbps 国外网络下裸 build Python Docker 镜像**. 国内一定要预先把 apt + PyPI 都换成阿里云 / 清华 mirror. 这一条能省 90 分钟.

---

## §1 部署当时的环境基线

### 1.1 服务器 (阿里云 ECS, 包年包月一年)

```
实例 ID:        i-0jlc2japx3qpc3w3b6ix
实例名:         iZ0jlc2japx3qpc3w3b6ixZ
规格:          ecs.e-c1m1.large (2 vCPU + 2 GiB)
区域:          华北 6 (乌兰察布) C
公网 IP:       8.130.156.2 (固定带宽 3 Mbps, 不是 EIP, 解绑实例就丢)
私网 IP:       172.18.101.20
系统:          Ubuntu 22.04 64 位
系统盘:        ESSD Entry 40 GiB
到期:          2027-04-30 (1 年)
费用:          ~¥700/年 (含一次性优惠)
```

> **设计考量**:
> - 2 GiB RAM 偏紧, 不够全栈; 加了 2 GiB swap (`/swapfile`) 兜底
> - 3 Mbps 是首次部署的**真痛点** ─ 镜像 / 包下载慢, 但日常运行 (FastAPI 响应) 完全够
> - Ulanqab 节点离北京近, 国内用户访问 ping ~30-50ms, 海外用户慢 ─ 不重要, 我们目标用户全国内
> - **不买 EIP**: 节省 ¥18/月; 代价是停机后 IP 可能变, 改下 DNS A 记录就行

### 1.2 域名 (阿里云 万网, 已购未备案)

```
域名:          xgzh.top
费用:          ~¥10-20/年
ICP 备案:     ⏳ 进行中 (提交日期: 待用户确认; 周期 7-21 工作日)
DNS A 记录:   ⏳ 待加 (api.xgzh.top → 8.130.156.2)
```

> **关键认知**: **没有 ICP 备案就不能正式上微信小程序**. 备案没通过前, 我们用 `IP:8000` 内测; 备案通过后才上 Caddy + HTTPS + 微信白名单 + 提审.

### 1.3 微信小程序 (个人主体)

```
AppID:         wxe525868b30a43b96
AppSecret:     1c25b6d01353d6be4e672186660f5f1b   ← ⚠️ 已泄漏, 部署完务必重置
主体类型:      个人
版本号:        v1.0.0 (versionCode 100)
当前状态:      未提审, 等后端 HTTPS + 备案就绪后提交
```

### 1.4 LLM (智谱 BigModel, 免费 GLM-4-Flash)

```
Provider:       zhipu (BigModel.cn)
Key:            f32e8c95...4NOEBH2y   ← ⚠️ 已泄漏, 部署完务必重置
主聊天 model:   zhipu/glm-4-flash (免费, 8K context)
情感打标:       zhipu/glm-4-flash (免费)
TL;DR:          zhipu/glm-4-flash (免费)
Embedding:      ❌ 暂未配 (需 SiliconFlow, MVP 跳过, 招股书 RAG 暂不可用)
Reranker:       ❌ 暂未配 (RAG_USE_RERANK=false 走 RRF 兜底)
```

> **决策**: V1 上线**不带招股书 RAG**, 等 DAU > 100 再花 ¥30 充硅基流动接 embedding. 主聊天 + IPO 日历 + 文章流就够 MVP 验证.

---

## §2 部署 Phase 时间线

```
12:10  阿里云 ECS 启动, 重置 root 密码
12:18  Workbench 远程登录, 改 sshd_config 允许密码登录
12:25  apt-get update + 装 curl/git/ufw/htop/vim       ─┐
12:30  fallocate 2G swap                                ├─ Phase A
12:31  时区改 Asia/Shanghai                             │   "系统初始化"
12:33  装 Docker (curl get.docker.com), 阿里云 mirror   │
12:35  UFW 规则: 允许 22 + 8000                         │
12:36  阿里云控制台安全组: 同上                         │
12:37  docker run hello-world ✅                       ─┘

12:40  git clone https://github.com/youzi530/xgzh.git  ─┐
12:43  生成 4 个 secrets (openssl rand -hex)           │
12:45  写 /opt/xgzh/apps/api/.env (148 行 / 110 keys)   ├─ Phase B
12:48  填 WECHAT_MP_APP_SECRET (微信公众平台拿)         │   "代码 + 配置"
12:50  写 /opt/xgzh-prod/docker-compose.yml             │
       (3 服务, 删掉 meilisearch, 内存限额)             │
12:52  改 Dockerfile: COPY uv.lock + alembic/          ─┘

12:55  docker compose up -d --build                    ─┐
       ↓                                                │
13:05  apt-get 装 build-essential 还在 770s              │
14:38  apt-get 终于跑完了 (6430s = 1h47min)              ├─ Phase C
14:40  uv sync 在装 122 个 PyPI 包                      │   "build & 踩坑"
14:48  uv sync 失败: README.md missing                  │   ❶❷❸
14:52  改 Dockerfile 加 COPY README.md                  │
14:56  重 build, 复用 cache, uv sync 一次过 ✅          │
15:23  api 容器 health: starting 一直没好               │
15:27  发现 uv run 在 runtime 重新装 mypy + ruff       │
15:30  改 docker-compose api.command 直调 venv binary   │
15:31  /healthz 返回 200 ✅                            ─┘
```

**真实测下来**: 12:10 → 15:31 = **3 小时 21 分钟**. 其中 **6430s (107 min) 全花在 apt-get 第一次下载**, 后续几乎是即时操作.

---

## §3 Phase A · 系统初始化 (~10 min 真敲键盘)

### 3.1 重置 root 密码 (Aliyun 控制台)

阿里云控制台 → ECS → 我的实例 → 选实例 → 实例操作 → 重置实例密码. 重启实例生效.

### 3.2 启用 SSH 密码登录 (Aliyun Workbench)

Aliyun Workbench (网页 SSH) 登录后:

```bash
# 默认 sshd 不让 root 用密码, 改:
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
systemctl restart ssh

# 之后从 Mac 直接 SSH 进:
# ssh root@8.130.156.2
```

> **生产强化** (后置 TODO): 改成 SSH key 登录 + 禁密码, 加 fail2ban. 当前 root + 密码 + 0.0.0.0:22 是攻击面, 短期接受, **48h 内必须改**.

### 3.3 系统更新 + 装包

```bash
apt-get update && apt-get upgrade -y
# (碰到 daemon-restart 蓝屏, Tab 选 Ok 接受默认)

apt-get install -y curl ca-certificates git ufw vim htop
# 全是 0.5s, 因为 Aliyun apt 源已经换好了
```

### 3.4 加 swap (2 GiB RAM 必做)

```bash
fallocate -l 2G /swapfile
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
free -h
# 期望: Swap: 2.0Gi 0B 2.0Gi
```

### 3.5 时区

```bash
timedatectl set-timezone Asia/Shanghai
date  # 期望: ... CST 2026
```

---

## §4 Phase A.5 · Docker + 镜像加速

### 4.1 装 Docker

```bash
curl -fsSL https://get.docker.com | sh
docker --version          # Docker version 29.4.1
docker compose version    # Docker Compose version v5.1.3
```

> 这条 `get.docker.com` 比想象中快 (~30s), 因为脚本本身小 (~10KB), Docker 的 deb 包阿里云 mirror 有.

### 4.2 配 Docker 镜像加速 (拉镜像快)

```bash
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'EOF'
{
  "registry-mirrors": [
    "https://docker.mirrors.aliyuncs.com",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
EOF
systemctl daemon-reload
systemctl restart docker
docker info | grep -A 6 "Registry Mirrors"
# 期望看到 3 个 mirror 列出来
```

> ⚠️ **重要警告**: 这只加速**拉镜像** (`docker pull`), **不加速容器内部联网** (e.g. 容器内 `apt-get install` 还是走 `deb.debian.org`). Phase C 我们就在这上面栽了 107 分钟跟头.

### 4.3 验证

```bash
docker run --rm hello-world
# 期望: "Hello from Docker!"
```

---

## §5 Phase A.7 · 防火墙 + 安全组 (双层)

### 5.1 服务器内 UFW (容器宿主防火墙)

```bash
ufw allow OpenSSH
ufw allow 8000/tcp comment "API temp, before ICP done"
ufw default deny incoming
ufw default allow outgoing
ufw --force enable
ufw status verbose
```

### 5.2 阿里云控制台安全组 (云上防火墙)

控制台 → ECS → 实例 → 安全组 → 配置规则 → 入方向 → 添加:

| 协议类型 | 端口范围 | 授权对象 | 优先级 | 描述 |
|---|---|---|---|---|
| TCP | 22/22 | 0.0.0.0/0 | 1 | SSH (默认有) |
| TCP | 8000/8000 | 0.0.0.0/0 | 100 | API 临时, 备案后改 443 |

> **双层防火墙都得配** ─ Aliyun 安全组在网卡前, UFW 在 OS 内. 两道任一关一关都不通.
>
> ⚠️ **备案下来后**: 删 8000, 加 80 + 443 (Caddy 用).

---

## §6 Phase B · 代码 + 配置

### 6.1 git clone

```bash
mkdir -p /opt
cd /opt
git clone https://github.com/youzi530/xgzh.git
ls /opt/xgzh/  # 期望看到 apps/ docs/ infra/ packages/ 等
```

> **私有 repo 备用方案**: 创建 GitHub PAT (scopes: `repo`), 用 `git clone https://<token>@github.com/youzi530/xgzh.git`, 然后 `git config credential.helper store` 让以后 `git pull` 不再问密码. 我们这次 repo 是公开的, 没用上.

### 6.2 生成 secrets + 写 .env

```bash
JWT_SECRET=$(openssl rand -hex 32)
PG_PASS=$(openssl rand -hex 16)
ADMIN_TOKEN=$(openssl rand -hex 32)

# 备份到 /opt/xgzh-secrets.txt (chmod 600), 防忘
cat > /opt/xgzh-secrets.txt <<EOF
# 生成于 $(date)
JWT_SECRET=$JWT_SECRET
POSTGRES_PASSWORD=$PG_PASS
OPS_ADMIN_TOKEN=$ADMIN_TOKEN
EOF
chmod 600 /opt/xgzh-secrets.txt

# /opt/xgzh/apps/api/.env 完整版见 git 不入版本管理, 关键字段:
# - APP_ENV=prod
# - LLM_PRIMARY_MODEL=zhipu/glm-4-flash    ← 免费
# - LLM_FALLBACK_MODEL=zhipu/glm-4-flash
# - ZHIPU_API_KEY=<bigmodel.cn 的 key>
# - DATABASE_URL=postgresql+asyncpg://xgzh:$PG_PASS@postgres:5432/xgzh
#                                                 ↑ docker network 内 hostname
# - REDIS_URL=redis://redis:6379/0
# - JWT_SECRET=$JWT_SECRET
# - WECHAT_MP_APP_ID=wxe525868b30a43b96
# - WECHAT_MP_APP_SECRET=<微信公众平台拿>
# - SMS_ADAPTER=mock                         ← v1 不发真短信
# - WECHATPAY_DEV_MODE=true                  ← 个人版永久 stub
# - SCHEDULER_ENABLED=true
# - RAG_USE_RERANK=false                     ← 没 SiliconFlow, 走 RRF 兜底
# - SENTRY_DSN= (空)                         ← v1 暂不用
# - ALERT_DINGTALK_WEBHOOK= (空)             ← v1 暂不用

chmod 600 /opt/xgzh/apps/api/.env
wc -l /opt/xgzh/apps/api/.env  # 148
grep -c "^[A-Z_]" /opt/xgzh/apps/api/.env  # 110 个 key
```

### 6.3 微信 AppSecret (从微信公众平台获取)

[https://mp.weixin.qq.com](https://mp.weixin.qq.com) → 登录 → **开发管理 → 开发设置**:

- AppID: 显示
- **AppSecret**: 点 **重置** → 短信验证 → 复制保存 (只显示一次)

```bash
# 替进 .env (sed 一行):
sed -i 's|WECHAT_MP_APP_SECRET=PLACEHOLDER_NEED_TO_FILL|WECHAT_MP_APP_SECRET=<真实 secret>|' /opt/xgzh/apps/api/.env
```

---

## §7 Phase B.5 · 写 prod docker-compose

`/opt/xgzh-prod/docker-compose.yml` 设计要点:

1. **3 服务**: postgres (pgvector/pg16) / redis (7-alpine) / api (本地 build)
2. **不暴露 PG / Redis 端口到宿主**, 只走 docker network (安全)
3. **内存限额** (2 GiB 物理 + 2 GiB swap):
   - postgres: 512M
   - redis: 320M (内含 maxmemory 256M)
   - api: 900M
4. **数据卷在 `/opt/xgzh-data/`** (repo 之外, 防 git pull 误删)
5. **`depends_on healthy`** 让 api 等 PG ready 再启 (alembic 不抢跑)
6. **api.command 跑 alembic 再 uvicorn** (启动迁移自动化)
7. **跳过 meilisearch** (后端代码 0 处使用, 省 200M RAM)

完整文件见 `/opt/xgzh-prod/docker-compose.yml`. 关键片段:

```yaml
api:
  build:
    context: /opt/xgzh/apps/api
    dockerfile: Dockerfile
  env_file:
    - /opt/xgzh/apps/api/.env
  ports:
    - "8000:8000"
  depends_on:
    postgres: { condition: service_healthy }
    redis:    { condition: service_healthy }
  command: >
    sh -c "/app/.venv/bin/alembic upgrade head &&
           /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
  deploy:
    resources:
      limits:
        memory: 900M
```

> ⚠️ **注意 command 里走 `/app/.venv/bin/`** ─ 不要用 `uv run`, 见 Phase C 坑 ❸.

---

## §8 Phase C · build & 三个真坑

### 坑 ❶ · apt-get 装 build-essential 1h47min

**现象**: `[3/9] RUN apt-get install build-essential curl ca-certificates` 这一步在 buildkit 里跑了 **6430s (107 min)**.

**原因**: 容器里 apt 走 `deb.debian.org` (在欧美), 我们的 `/etc/docker/daemon.json` 镜像加速**只加速 `docker pull`**, 不加速容器内联网. 阿里云乌兰察布 → debian 国外服务器, 3 Mbps 带宽, 87 MB 包逐字节挤过来.

**事后修法** (写进 Dockerfile, 已 commit):

```dockerfile
# /etc/apt/sources.list.d/debian.sources 是 Debian 13 (trixie) 的 deb822 格式
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
      sed -i 's@deb.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources && \
      sed -i 's@security.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources; \
    fi
```

**踩坑当时怎么挺过去**: 没动它, 让它跑完 ─ 因为**这层 buildkit 会 cache**, 一次性付 107 min, 后续 build 复用. 修法是"下一次重新搭服务器时让它别再来一次".

**经验**: 国内 server build Python 镜像, **必须**预先在 Dockerfile 里把 apt + PyPI mirror 都换成阿里云 / 清华. 当前 repo `apps/api/Dockerfile` 已包含此修复.

### 坑 ❷ · uv sync 报 "Readme file does not exist: README.md"

**现象**: build 跑到 `[6/9] RUN uv sync --frozen --no-dev`, 122 个 PyPI 包都下完了 (~5-9 min), 然后报错挂掉:

```
× Failed to build `xgzh-api @ file:///app`
├─▶ The build backend returned an error
╰─▶ Call to `hatchling.build.build_editable` failed (exit status: 1)
    [stderr]
    OSError: Readme file does not exist: README.md
```

**原因**: `pyproject.toml` 第 5 行 `readme = "README.md"`, hatchling 在做 editable install 校验 metadata 时找不到文件. Dockerfile 之前只 COPY 了 `pyproject.toml` + `app/` + `alembic/`, **没 COPY `README.md`**.

**修法** (1 行, 已 commit):

```dockerfile
# 改:
COPY pyproject.toml uv.lock* ./
# 成:
COPY pyproject.toml uv.lock* README.md ./
```

**为什么之前 dev 跑 `make ci-integration` 没问题**: 本地是直接 `uv sync` 在 repo 根跑, README.md 一直在; 是**走 Dockerfile 的部署路径**才暴露了这个 bug.

### 坑 ❸ · 容器启动后 uv run 重新装 mypy + ruff, 永远不进 alembic

**现象**: 镜像 build 成功后启容器, 看日志:

```
xgzh-api  |    Building xgzh-api @ file:///app
xgzh-api  | Downloading mypy (14.1MiB)
xgzh-api  | Downloading ruff (10.8MiB)
xgzh-api  |       Built xgzh-api @ file:///app
```

只有这 4 行就停在那里, port 8000 听着 (curl `Connection reset`), 但**根本没跑 alembic + uvicorn**.

**原因**: docker-compose 里 command 用 `uv run alembic upgrade head && uv run uvicorn ...`. `uv run` 默认会在每次启动**校验环境**, 发现镜像里的 venv 没 dev deps (Dockerfile 里 `--no-dev` 装的), 就触发隐式 `uv sync` (含 dev). 装 mypy + ruff 走 PyPI 默认源 (不是阿里云 mirror), 在 3 Mbps 上每次启动要 5-10 min, 卡死在那儿.

**修法** (1 处改, 已应用到 `/opt/xgzh-prod/docker-compose.yml`):

```yaml
# 改:
command: >
  sh -c "uv run alembic upgrade head &&
         uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"

# 成:
command: >
  sh -c "/app/.venv/bin/alembic upgrade head &&
         /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
```

绕过 `uv` 在 runtime 的环境校验, 直接走 venv 里的 binary.

**alternative 方案** (备忘, 更优雅但没用上): 在镜像 ENV 里加 `UV_NO_SYNC=1`, 让 `uv run` 跳过 sync 校验. 下次清场重 build 时考虑.

---

## §9 Phase D · 启动 + 验证

### 9.1 启动

```bash
cd /opt/xgzh-prod
docker compose up -d --force-recreate api
sleep 30
```

### 9.2 健康检查

```bash
docker compose ps
# 期望: 3 个全 (healthy)

curl -s http://127.0.0.1:8000/healthz; echo ""
# 期望: {"status":"ok","app":"xgzh-api","env":"prod","llm_configured":true}

docker compose logs --tail=80 api 2>&1 | tail -50
# 期望看到:
# - alembic 跑 0001 → 0015 全部 Running upgrade
# - tool_registry.register 6 次 (basic_info / financial / historical / hybrid_search / peers / sentiment)
# - scheduler.jobs_registered (7 个 cron job)
# - Cache: using RealRedisClient
# - Application startup complete
# - Uvicorn running on http://0.0.0.0:8000
```

### 9.3 实战日志摘录 (15:31:36 真实时刻)

```
INFO  [alembic.runtime.migration] Running upgrade  -> 0001_init
... (13 行 migration log) ...
INFO  [alembic.runtime.migration] Running upgrade 0014_community -> 0015_ipos_price_range

tool_registry.register name=get_ipo_basic_info timeout=5.0s
tool_registry.register name=get_financial_statements timeout=5.0s
tool_registry.register name=get_historical_winning_rate timeout=5.0s
tool_registry.register name=hybrid_search timeout=15.0s
tool_registry.register name=get_peer_comparison timeout=5.0s
tool_registry.register name=get_sentiment_summary timeout=3.0s

INFO:     Started server process [11]
INFO:     Waiting for application startup.
{"msg": "app.start name=xgzh-api env=prod"}
{"msg": "sentry.skipped (SENTRY_DSN 未配置, 不初始化)"}
{"msg": "scheduler.jobs_registered a:initial_delay=30s cron=8,20 ... | hk:initial_delay=60s cron=9,17 ..."}
{"msg": "scheduler.started timezone=Asia/Shanghai"}
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     172.19.0.1:58392 - "GET /healthz HTTP/1.1" 200 OK
```

✅ **Phase D 通过**.

---

## §10 当前状态 (2026-04-30 16:00)

### ✅ 已完成

- [x] 阿里云 ECS 开通 + SSH + 双层防火墙
- [x] Docker + 镜像加速
- [x] Code clone + secrets 生成 + .env (110 keys)
- [x] docker-compose 3 服务编排
- [x] Build 镜像 (踩 3 个坑全部解决)
- [x] 容器启动 + alembic 0001→0015 全迁移
- [x] LLM (Zhipu) 配置 + healthz 200

### ⏳ 进行中

- [ ] **公网可达验证** (Mac 上 `curl http://8.130.156.2:8000/healthz`)
- [ ] **DNS A 记录**: `api.xgzh.top` → `8.130.156.2` (5 min, 立刻可做)
- [ ] **ICP 备案** (硬等 7-21 工作日, 阻塞 HTTPS + 微信白名单 + 提审)
- [ ] **LLM 实战测试** (真发一次 chat 请求看 Zhipu key 工作)
- [ ] **Scheduler 干活验证** (`SELECT COUNT(*) FROM ipos` 等 IPO ingest 跑过)

### 🔴 P0 安全债 (48h 内必修)

1. **Zhipu API key 泄漏在对话**: `f32e8c95...4NOEBH2y` ─ 重置 + sed 替进 .env
2. **微信 AppSecret 泄漏在对话**: `1c25b6d0...0f5f1b` ─ 公众平台重置
3. **SSH root + 密码登录开着**: 改成 SSH key + 禁密码 + 装 fail2ban
4. **阿里云 Workbench 不该开**: 用完关掉, 默认开放是攻击面
5. **`OPS_ADMIN_TOKEN` 在 secrets.txt 里**: 这个反而比泄漏在 chat 里好一点 (chmod 600), 但还要 backup 到密码管理器, 不能丢

### 🟡 P1 待办 (备案完成前可做)

1. **/admin/* 端点压测** (用 OPS_ADMIN_TOKEN 跑 feature flag 接口)
2. **每日 pg_dump cron** (灾备)
3. **uptime 监控** (uptimerobot.com 免费版, 5min ping 一次 healthz)
4. **日志 rotate 配置** (loguru / docker logs 都会涨)

### 🟢 备案下来后做

1. **Caddy 反代 + Let's Encrypt HTTPS**
2. **删 ufw 8000 / 加 80 + 443**
3. **微信小程序 → 服务器域名白名单**
4. **小程序提交体验版 → 提审**

---

## §11 经验沉淀 (给下次的自己)

### 11.1 时间预算 (国内 3 Mbps server, 真实数字)

| 阶段 | 预期 | 实际 | 偏差原因 |
|---|---|---|---|
| 服务器开通 + Phase A 系统初始化 | 20 min | 25 min | 改 sshd 折腾 5 min |
| Phase A.5 Docker | 5 min | 5 min | ✅ |
| Phase B 代码 + .env | 10 min | 10 min | ✅ |
| Phase B.5 docker-compose | 5 min | 5 min | ✅ |
| **Phase C build** | **10 min** | **120 min** | **apt 装包 107 min + uv 9 min** |
| Phase D 启动 + 验证 | 5 min | 30 min | uv run 重 sync 坑 + 改 command |
| **总** | **55 min** | **~3h 20min** | apt 慢 + 2 个 Dockerfile bug |

**下次预期** (Dockerfile 已修): **40-60 min** 整套搭完.

### 11.2 必须 Dockerfile 内置的 4 件事

```dockerfile
# 1. 阿里云 apt mirror (省 100 min)
RUN sed -i 's@deb.debian.org@mirrors.aliyun.com@g' /etc/apt/sources.list.d/debian.sources

# 2. 阿里云 PyPI mirror (省 5-15 min)
ENV UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

# 3. COPY README.md 不能省 (hatchling editable install 必需)
COPY pyproject.toml uv.lock* README.md ./

# 4. COPY alembic + alembic.ini (容器内跑迁移必需)
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini
```

### 11.3 必须 docker-compose 内置的 3 件事

```yaml
# 1. command 直接走 venv binary, 不要 uv run
command: >
  sh -c "/app/.venv/bin/alembic upgrade head &&
         /app/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"

# 2. depends_on healthy (alembic 不抢跑)
depends_on:
  postgres: { condition: service_healthy }

# 3. 内存限额 (2 GiB host 不爆)
deploy:
  resources:
    limits:
      memory: 900M
```

### 11.4 容器栈不要 meilisearch (省 200M RAM)

后端代码 0 处 `import meili` / `from meilisearch`, 全文检索走 PG GIN. 删. 等真要做 facet 搜索 / 多字段权重排序时再加.

### 11.5 国内不要租海外服务器 build Docker

哪怕海外节点更便宜, 国内访问镜像源 / PyPI / Debian repo / Docker Hub 几乎都要走代理. 坑 ❶ 的 107 min 直接告诉你这个代价.

### 11.6 灾后重建 SOP (≤ 1 小时)

如果服务器爆了要从零重新搭:

```bash
# 假设新 Aliyun ECS 已开通, root 密码已知, ssh 进去:

# Phase A (~10 min)
apt-get update && apt-get install -y curl ca-certificates git ufw vim htop tmux
fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
timedatectl set-timezone Asia/Shanghai

# Phase A.5 (~5 min)
curl -fsSL https://get.docker.com | sh
mkdir -p /etc/docker && cat > /etc/docker/daemon.json <<'EOF'
{"registry-mirrors":["https://docker.mirrors.aliyuncs.com","https://hub-mirror.c.163.com"]}
EOF
systemctl restart docker

# Phase A.7 (~2 min)
ufw allow 22 && ufw allow 8000/tcp && ufw default deny incoming && ufw --force enable

# Phase B (~5 min, 假设 GitHub PAT 已存在)
mkdir -p /opt && cd /opt
git clone https://github.com/youzi530/xgzh.git

# 从你的密码管理器把 .env + secrets.txt 还原到位 (灾备前 backup 过), 否则:
# 1. 重新生成 4 个 secrets, 2. 重新拿 Zhipu / WeChat secret, 3. 写 .env

# Phase B.5 (~3 min)
mkdir -p /opt/xgzh-prod /opt/xgzh-data/postgres /opt/xgzh-data/redis
# 复制 docker-compose.yml (从备份 / 从仓库历史拉)

# Phase C (~10-15 min, Dockerfile 已含 mirror)
cd /opt/xgzh-prod
tmux new -s build
docker compose build api 2>&1 | tee /tmp/build.log

# Phase D (~3 min)
docker compose up -d
sleep 30
curl -s http://127.0.0.1:8000/healthz
```

**如果数据库要还原**: PG dump 文件还原到 `/opt/xgzh-data/postgres` 之前, `docker exec ... pg_restore`. 这个流程没在今天跑过, 待第一次真灾备演练时补.

---

## §12 留个尾巴 / 下一阶段地图

```
今天 (4-30):  ✅ 后端容器栈起来 + IP:8000 内可达
明天 (5-1):   ⏳ DNS A 记录生效, IP / 域名内测验证
                假期: 内测白名单跑业务 P0 (登录 / 列表 / 详情 / AI / VIP)

ICP 备案 +0 工作日:  Caddy + HTTPS + 微信小程序服务器域名白名单
ICP 备案 +1 工作日:  小程序提审 (个人版 + 金融领域 = 高敏感, 见 [`01-release-plan.md`](./01-release-plan.md))
ICP 备案 +3-7 日:    审核通过, 5% 灰度发布
ICP 备案 +10 日:     灰度 100% 全量

后续优化 (无时间表):
- LLM 升级到 SiliconFlow 接 embedding + rerank (招股书 RAG 启用)
- 接 Sentry 错误追踪
- 接钉钉机器人告警
- 加 Grafana / Prometheus 看面板
- 数据库每日自动 pg_dump 灾备
- 升级 4G RAM 实例 (DAU > 500 时)
```

---

## 附 · 文件 / 目录索引 (服务器上)

| 路径 | 内容 | 备份策略 |
|---|---|---|
| `/opt/xgzh/` | git clone 的代码仓库 | 不需要, GitHub 是 source of truth |
| `/opt/xgzh-prod/docker-compose.yml` | prod 版编排 | **必须备份** (repo 不含此版) |
| `/opt/xgzh-prod/.env` (未来如果有) | docker compose 变量替换用 | 看是否有内容 |
| `/opt/xgzh/apps/api/.env` | API runtime 110 keys | **必须备份**, 含 secret |
| `/opt/xgzh-secrets.txt` | JWT / PG / Admin token 备忘 | **必须备份**, 600 权限 |
| `/opt/xgzh-data/postgres/` | PG 数据卷 | **必须每日 pg_dump** |
| `/opt/xgzh-data/redis/` | Redis 数据卷 | 不重要 (cache + rate limit), 重启可重建 |

---

> **维护人**: youzi530
> **下次更新**: ICP 备案下来 / Caddy 配好 / 小程序提审通过这三个里程碑各自记一笔
> **关联文档**:
> - [`00-backend-deploy-spike.md`](./00-backend-deploy-spike.md) — 部署前的方案对比 (3 档选 1)
> - [`02-release-runbook.md`](./02-release-runbook.md) — 小程序端从 build 到提审的 SOP
> - [`04-rollback-plan.md`](./04-rollback-plan.md) — 出事时的回退手册
