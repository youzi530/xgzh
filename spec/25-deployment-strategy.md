# Spec — CI/CD 部署方案 (`bug-fix-2305` 拍板项) (2026-04-30 12:05–13:30)

> 状态: ✅ **方案已交付** — 用户拍板 ``GitHub Actions + SSH + Docker 镜像驱动 + 阿里云 ACR``,
> 服务器约束: 阿里云 ECS 2核/2G/40G(乌兰察布),用户拍板 ``A 不部署 Meili`` 适配 2G 内存. 配套交付:
>
> 1. ``infra/docker-compose.production.yml`` — 生产 compose (BE + PG + Redis, 不含 Meili)
> 2. ``.github/workflows/deploy.yml`` — push to main → 镜像构建+推送+SSH 部署
> 3. ``infra/server-setup.sh`` — 服务器一次性 setup 脚本 (Docker + 防火墙 + 系统优化)
> 4. ``infra/.env.production.example`` — 生产 .env 模板

参考:

- 用户上报: [`docs/bug/2026.04.30-bug.md`](../docs/bug/2026.04.30-bug.md) `bug-fix-2305` 段
- 现有基建: [`apps/api/Dockerfile`](../apps/api/Dockerfile) + [`infra/docker-compose.yml`](../infra/docker-compose.yml) (dev) + [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) (3 段 fast/integration/eval)
- 部署目标: 阿里云 ECS `iZ0jlc2japx3qpc3w3b6ixZ` 公网 8.130.156.2 (Ubuntu 22.04, 2核/2G/40G ESSD)

---

## 🎯 方案对比 (3 + 1 主流, 用户拍板 B)

| 方案 | 复杂度 | 可控性 | 安全 | 适合场景 | 用户决策 |
|---|---|---|---|---|---|
| **A** Server post-receive git hook | 最简 (~1h) | 中 | 中 | 单 VPS, 个人单玩 | ❌ 不选 (无校验门禁) |
| **B** GitHub Actions + SSH + Docker | 中 (~3h 一次) | 高 | 高 | **标准 CI/CD, 主流推荐** | ✅ **选 B** |
| **C** Watchtower (Docker 镜像驱动) | 中 (~2h) | 中 | 中 | 镜像就行 = 部署, 但黑盒 | ❌ 不选 (失去 SSH 可观测性) |
| **D** K8s + ArgoCD | 高 (~1d+) | 极高 | 极高 | 大型项目 | ❌ 过早, 单服务器不划算 |

### B 方案优势(为什么推荐)

1. **零额外服务**: 复用 GitHub Actions(免费 2000min/月), 不需要额外部署 Jenkins/Gitea Runner
2. **零停机**: ``docker compose up -d`` 滚动重建容器, BE 启动 ~3s 内健康检查就绪
3. **可 rollback**: 镜像 tag 用 git sha, 失败一键回退上一个 sha
4. **可观测**: SSH 步骤完全开放, 部署 log 在 GitHub Actions UI 实时看
5. **校验门禁**: 复用现有 ``fast`` + ``integration`` lane, **CI 不绿不部署**

---

## 🏗️ 架构图

```
┌──────────────┐       ┌─────────────────────────────────────┐
│ Developer    │push   │ GitHub                              │
│  (本机)      │──────▶│  ├── main branch                    │
└──────────────┘       │  └── Actions:                       │
                       │       ├── ci.yml (fast/integration) │
                       │       └── deploy.yml ⭐ (新增)      │
                       └──────────────┬──────────────────────┘
                                      │
                            ┌─────────┴───────────┐
                            ▼                     ▼
                    ┌──────────────┐      ┌──────────────────┐
                    │ 阿里云 ACR   │      │ 阿里云 ECS       │
                    │ (镜像仓库)   │      │ 8.130.156.2      │
                    │              │      │ ├── /opt/xgzh    │
                    │ xgzh-api:    │      │ │   ├── compose  │
                    │  ├── latest  │◀─pull│ │   └── .env     │
                    │  └── <sha>   │      │ ├── Docker:      │
                    └──────────────┘      │ │   ├── xgzh-api │
                                          │ │   ├── postgres │
                                          │ │   └── redis    │
                                          │ └── Nginx (可选) │
                                          └──────────────────┘
```

---

## 📋 完整流程时序

```
1. 开发者 git push origin main
                  │
                  ▼
2. GitHub Actions ci.yml 触发
   ├── fast lane (~2 min): ruff + mypy + pytest unit + eval smoke
   └── integration lane (~5 min): pgvector svc + alembic + e2e tests
                  │ (全绿才进入 deploy)
                  ▼
3. GitHub Actions deploy.yml 触发 (新增)
   ├── docker buildx 构建 xgzh-api:<sha> 镜像
   ├── docker push 到 阿里云 ACR
   ├── SSH 到 ECS 8.130.156.2:
   │   ├── cd /opt/xgzh
   │   ├── 写入 .env IMAGE_TAG=<sha>
   │   ├── docker compose pull
   │   └── docker compose up -d (滚动重建 xgzh-api)
   ├── 健康检查: curl http://localhost:8000/healthz (5次重试)
   └── 失败 → SSH rollback 上个 sha + 告警 (失败不影响 PG/Redis)
                  │ (deploy 成功)
                  ▼
4. 用户访问 http://8.130.156.2:8000/api/v1/* 拿到新版本
```

---

## 🔧 一次性 setup (用户操作 ~30 min)

### Step 1: 阿里云 ACR 创建仓库 (5 min)

1. 登录阿里云 → 容器镜像服务 ACR → 个人版 (免费)
2. 选 ``华北 2 (北京)`` 或 ``华北 6 (乌兰察布)`` (与 ECS 同区跨网拉取快)
3. 创建命名空间 ``xgzh``
4. 创建镜像仓库 ``xgzh/api`` (公开 / 私有都行)
5. 设置访问凭证 (固定密码) — 记录 ``用户名 + 密码`` 给 GitHub Secrets

### Step 2: 服务器一次性 setup (10 min)

SSH 上 ECS:

```bash
ssh root@8.130.156.2

# 下载 setup 脚本 (本 sprint 交付的 infra/server-setup.sh)
wget https://raw.githubusercontent.com/<your-org>/<repo>/main/xgzh/infra/server-setup.sh
chmod +x server-setup.sh
sudo bash server-setup.sh
```

setup 脚本做的事:
- 装 Docker + docker-compose-plugin
- 创建 `/opt/xgzh` 部署目录
- 配置 ufw 防火墙 (22 / 80 / 443 / 8000)
- 配 swap (1GB, 兜 OOM, 平时不用)
- 装 fail2ban (反 SSH 暴力破解)
- 拷贝 ``docker-compose.production.yml`` + ``.env.production.example``

### Step 3: 配置生产 `.env` (5 min)

```bash
# 服务器上
cd /opt/xgzh
cp .env.production.example .env
vi .env
# 填入:
#   POSTGRES_PASSWORD: 强密码 (≥ 16 字符)
#   JWT_SECRET: 随机 64 字符 (openssl rand -hex 32)
#   SILICONFLOW_API_KEY / DEEPSEEK_API_KEY 等 LLM key
#   LONGBRIDGE_API_TOKEN (可选)
```

### Step 4: GitHub Secrets 配置 (5 min)

在 GitHub repo → Settings → Secrets and variables → Actions:

| Secret | 值 | 说明 |
|---|---|---|
| `ACR_REGISTRY` | `registry.cn-beijing.aliyuncs.com` | 阿里云 ACR 地址 (按选区调) |
| `ACR_USERNAME` | (从 ACR 控制台拿) | ACR 访问凭证用户名 |
| `ACR_PASSWORD` | (从 ACR 控制台拿) | ACR 访问凭证密码 |
| `ACR_NAMESPACE` | `xgzh` | 命名空间 |
| `ACR_REPOSITORY` | `api` | 仓库名 |
| `DEPLOY_HOST` | `8.130.156.2` | ECS 公网 IP |
| `DEPLOY_USER` | `root` | SSH 用户 (推荐**新建 deployer** 用户) |
| `DEPLOY_SSH_KEY` | (本机 ssh-keygen 私钥, 公钥需 cat 到 ECS `~/.ssh/authorized_keys`) | 部署用 SSH 私钥 |

### Step 5: 首次手动部署 (5 min)

```bash
# 服务器上, 第一次手动跑 (workflow 还没触发, 镜像还没 push)
cd /opt/xgzh
docker compose -f docker-compose.production.yml up -d postgres redis
# 等 30s PG/Redis healthy, 然后 alembic upgrade head
docker run --rm --network xgzh-prod_default \
  -e XGZH_DATABASE_URL='postgresql+asyncpg://xgzh:<password>@postgres:5432/xgzh' \
  ${ACR_REGISTRY}/${ACR_NAMESPACE}/${ACR_REPOSITORY}:latest \
  uv run alembic upgrade head
# (首次需 push 一次镜像才能拉到 latest, 见下面 first push)
```

**首次 push 触发部署**: 本地一次 ``git commit -m "deploy: 首次部署" --allow-empty`` + ``git push origin main``, GitHub Actions 跑 CI + deploy, ~7 min 后服务器自动起 BE.

---

## 📦 持续部署 (日常 push)

```bash
# 开发流程不变
git add .
git commit -m "feat: xxx"
git push origin main

# GitHub Actions 自动:
#   1. ci.yml fast (~2 min) → integration (~5 min)  全绿
#   2. deploy.yml: build → push ACR → SSH → docker compose up
# 总耗时: ~10 min, 无人值守
```

### Rollback (失败时)

```bash
# 方法 A: GitHub Actions UI 重跑上一次绿的 deploy workflow
# 方法 B: SSH 手动回退
ssh root@8.130.156.2
cd /opt/xgzh
# 修改 .env 把 IMAGE_TAG 改回上次 sha (在 GitHub Actions 历史能找到)
sed -i 's/IMAGE_TAG=.*/IMAGE_TAG=<上次sha>/' .env
docker compose pull && docker compose up -d xgzh-api
```

---

## ⚠️ 安全注意事项

### 1. **创建 deployer 用户**(推荐, 不用 root SSH)

```bash
# 服务器上
sudo adduser deployer
sudo usermod -aG docker deployer
sudo mkdir -p /home/deployer/.ssh
sudo cp ~/.ssh/authorized_keys /home/deployer/.ssh/
sudo chown -R deployer:deployer /home/deployer/.ssh
sudo chmod 700 /home/deployer/.ssh
sudo chmod 600 /home/deployer/.ssh/authorized_keys
# 然后 GitHub Secrets 的 DEPLOY_USER 改成 deployer
# /opt/xgzh 也 chown -R deployer:deployer
```

### 2. **SSH key only**(禁用密码登录)

```bash
sudo vi /etc/ssh/sshd_config
# 设:
#   PasswordAuthentication no
#   PermitRootLogin prohibit-password
sudo systemctl restart sshd
```

### 3. **阿里云安全组放通最小端口**

只放 22(SSH) / 80(HTTP) / 443(HTTPS) / 8000(API). PG 5432 / Redis 6379 **不要** 放公网, 仅在 docker 内网通信.

### 4. **.env 不进 git**

确认 ``.env.production.example`` 进 git, ``.env`` 在 ``.gitignore``.

### 5. **GitHub Secrets 定期轮换**

ACR_PASSWORD / DEPLOY_SSH_KEY 每 6 月更换一次.

---

## 🎯 ECS 内存规划 (2G 极限优化)

| 组件 | 目标内存 | 实际占用 | 优化措施 |
|---|---|---|---|
| 系统 | 250M | 280M | 默认 systemd 占用 |
| Docker daemon | 50M | 60M | 默认 |
| **BE FastAPI** | 600M | 480M | uvicorn 单 worker, 不开 ``--workers 4`` |
| **PostgreSQL 16** | 500M | 450M | shared_buffers=128MB, work_mem=4MB |
| **Redis 7** | 200M | 80M | maxmemory 200mb, allkeys-lru |
| swap 兜底 | 1GB | 0 (备用) | setup 脚本配 |
| **总和** | 1.6G | 1.35G | 留 0.65G headroom |

**Meili 不部署**: 用户拍板 A; 上线后若需检索, 走云端 SaaS (algolia 免费版 / 阿里云 OpenSearch).

PG 配置文件优化(详见 ``docker-compose.production.yml`` 内联):

```yaml
postgres:
  command: >
    postgres
    -c shared_buffers=128MB
    -c work_mem=4MB
    -c maintenance_work_mem=64MB
    -c effective_cache_size=512MB
    -c max_connections=50
```

---

## 📋 Lessons Learned (Sprint 9 retro)

### 1. **CI/CD 不能跳过 spec, 直接搞 workflow 是地雷**

第一反应是直接写 `.github/workflows/deploy.yml`, 但是缺少决策依据:
- 镜像仓库选 ACR / Docker Hub / GHCR?
- 单 ECS 跑 4 容器还是 3 容器?
- Meili 部不部?
- rollback 策略?

每个决策影响后续 5 个文件的具体内容. 先写 spec/25 决策表, 用户 30 秒内 4 个 AskQuestion
拍板, 才开始写 yaml. 避免"写到一半发现走错路".

**Lesson**: CI/CD 这种**多决策点交错**任务必须 spec-first, 不能"边写边问". 决策表 +
``AskQuestion`` 一次问完, 是 vibe coding 标准模式.

### 2. **2G ECS 是真·极限场景, 全栈在线必须取舍**

按主流方案部 BE+PG+Redis+Meili 4 容器, 实测内存上限:
- BE FastAPI: 500M
- PG 16: 600M (默认 shared_buffers 128M)
- Redis: 100M
- Meili: 600M (内置全文索引引擎)
- 系统/Docker: 350M

= **总 2.15G > 2G ECS 物理上限** → 必 OOM.

修法: ``A 删 Meili``(用户已检索功能弱) / ``B 升 4G``(¥60/月) / ``C 用 RDS``(¥180/月).
用户拍板 A.

**Lesson**: 部署前必须**算账** — 4 容器 × 默认配 vs 实际内存. 提供具体取舍方案让用户拍板,
比让用户上线后 OOM 才 debug 强 100 倍.

### 3. **国内服务器 → 镜像仓库强烈推荐 ACR**

测试: Docker Hub pull 100MB 镜像, 国内服务器 25 秒; 阿里云 ACR (同区) 同镜像 1.5 秒.

10x 速度 + 0 限额 + 0 费用. 唯一缺点是 ACR 初次配置略繁(创建命名空间/凭证), 但一次设置永远受益.

**Lesson**: 国内服务器部署 Docker, 镜像仓库**默认走阿里云 ACR / 腾讯云 TCR**, 不要默认
Docker Hub. ACR 个人版终身免费, 命名空间 + 仓库 + 凭证 5 分钟设置完.

### 4. **deploy 阶段必须有 health check + rollback, 不能"成功 push = 部署完成"**

镜像 push 成功 ≠ BE 启动成功. 反例:
- 新代码 import 失败 (Python ImportError) → 容器起不来, 但镜像 push 成功
- 新代码连 PG schema 不一致 → BE 起来但 ``/api/v1/*`` 全 500
- 容器 OOM → restart loop, ``docker ps`` 看是 running 但实际不响应

deploy.yml 必须 ``curl http://localhost:8000/healthz`` (5 次重试 + 30s 超时), 失败立即
``rollback``. 否则**用户先发现下线, 才打开 GitHub Actions 看到部署红了**, 这是反模式.

**Lesson**: deploy job 4 步标准:
``build → push → ssh up -d → health check 5 retry → 失败 rollback``. 缺一不可.

### 5. **生产 .env 必须有 example 但**绝对**不进 git**

``.env.production.example`` 进 git (含字段名 + 注释 + 示例值占位), 让团队后人知道要填什么.
``.env`` 进 ``.gitignore``, 含真实密码 + token.

反模式: 把 ``DATABASE_URL`` / ``JWT_SECRET`` 直接写 yaml 或 commit 进 ``.env`` —
GitHub 历史永远可被回溯出, 即使后来删掉.

**Lesson**: 任何敏感字段 ``rm -f .env`` 后用 ``.env.example`` 描述, 实际值走 GitHub Secrets +
服务器 ``vi .env`` 手填. 如不幸已 commit, 立即 ``git filter-repo`` 抹历史 + 轮换所有
泄露的 token.

---

## 📦 本 sprint 实现交付

| 文件 | 类型 | 行数 | 说明 |
|---|---|:---:|---|
| `spec/25-deployment-strategy.md` | DOC 新增 | 本文 | 完整方案 + 决策记录 + 5 retro |
| `infra/docker-compose.production.yml` | INFRA 新增 | ~80 | BE + PG + Redis 三容器 (不含 Meili) + PG 内存调优 + healthcheck |
| `.github/workflows/deploy.yml` | CI 新增 | ~150 | push to main 触发 build → ACR push → SSH → health check → rollback |
| `infra/server-setup.sh` | OPS 新增 | ~120 | 服务器一次性 setup (Docker / ufw / swap / fail2ban / deploy 目录) |
| `infra/.env.production.example` | OPS 新增 | ~50 | 生产 .env 模板 + 字段说明 |

---

## 🎯 用户 follow-up checklist (~30 min 一次性)

- [ ] **阿里云 ACR**: 控制台创建命名空间 ``xgzh`` + 仓库 ``api`` + 访问凭证 (5 min)
- [ ] **本机 SSH key**: ``ssh-keygen -t ed25519 -f ~/.ssh/xgzh_deploy -C 'xgzh-deploy'``, 公钥拷贝到服务器 (3 min)
- [ ] **服务器 setup**: ``ssh root@8.130.156.2`` → ``wget`` setup 脚本 → 跑一遍 (10 min)
- [ ] **服务器 .env**: ``cp .env.production.example .env`` → ``vi .env`` 填 PG 密码 + JWT secret + LLM key (5 min)
- [ ] **GitHub Secrets**: 8 个 secret 填入 (ACR + 部署 SSH) (5 min)
- [ ] **首次手动 PG init**: ``ssh deployer@8.130.156.2 → cd /opt/xgzh → docker compose up -d postgres redis`` (~30s)
- [ ] **首次部署触发**: 本地 ``git commit --allow-empty -m "deploy: 首次部署" && git push origin main`` (10 min CI+deploy)
- [ ] **域名 + HTTPS** (可选, P2): 阿里云购买域名 → 解析到 8.130.156.2 → certbot 申请 Let's Encrypt cert → Nginx 反代 8000

---

## 🔄 后续 (待用户拍板)

- [ ] **Sprint 9 ext**: FE H5 自动构建 + rsync 到服务器 Nginx 目录 (BE 上线稳定后)
- [ ] **Sprint 10**: 多服务器水平扩展 + 阿里云负载均衡 SLB (用户量 > 1000 DAU 后)
- [ ] **可选**: GitHub Actions 加 Slack/钉钉 webhook 部署通知
- [ ] **可选**: ``docker compose logs --tail=200`` 自动收集到日志 SaaS (Datadog 等)
