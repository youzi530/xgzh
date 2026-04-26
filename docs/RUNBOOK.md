# XGZH RUNBOOK · 本地开发 + 三端部署手册

> 适用阶段：Sprint 2 收尾（2026-04-26 已落地）→ Sprint 3 启动期（变现闭环）
>
> 本文档目的：
> 1. **本地跑通**：让你（或新加入的协作者）在 1 小时内把 H5 / 微信小程序 / Android / iOS 四个端跑起来看 UI
> 2. **部署方案**：给出免费版（内测期）+ 付费版（正式上线）两套完整方案，精确到每条命令 / 价格 / 时间
> 3. **决策与坑**：把已知踩坑点 + 关键决策树记在最前面，避免每次重新踩
>
> 维护原则：每跑通一次新场景或踩到新坑，回填到对应 §坑表；账号申请流程变更时更新 §准备清单。

---

## 📋 目录

- [TL;DR · 快速决策树](#tldr--快速决策树)
- [准备清单](#准备清单)
- [本地三端跑通](#本地三端跑通)
  - [Phase 1：5 分钟跑 H5（最快）](#phase-15-分钟跑-h5最快)
  - [Phase 2：15 分钟跑微信小程序](#phase-215-分钟跑微信小程序)
  - [Phase 3：30 分钟跑 Android 真机](#phase-330-分钟跑-android-真机)
  - [Phase 4：30 分钟跑 iOS 模拟器](#phase-430-分钟跑-ios-模拟器)
  - [三端调试对照表](#三端调试对照表)
- [部署方案 A · 免费版（内测期 / 0-100 DAU）](#部署方案-a--免费版内测期--0-100-dau)
- [部署方案 B · 付费版（正式上线 / 1K-5K DAU）](#部署方案-b--付费版正式上线--1k-5k-dau)
- [关键决策清单](#关键决策清单)
- [已知坑速查表](#已知坑速查表)
- [附录](#附录)

---

## TL;DR · 快速决策树

```
你想做什么？
  │
  ├── 「先看眼 UI 长啥样」                       → §Phase 1 H5 路径（5 分钟）
  ├── 「在小程序 / 真机上感受一下」              → §Phase 2 / 3 / 4
  │
  ├── 「给朋友 / 内测用户用，没收入」            → 部署方案 A（年总成本 ~¥30-100）
  └── 「准备拉真实用户 / 收钱」                  → 部署方案 B（年总成本 ~¥6K-60K，看 DAU）
```

**最常见路径**：先 H5 5 分钟（确认审美）→ 小程序 15 分钟（确认核心生态）→ Android 标准基座 30 分钟（端适配）→ iOS 模拟器 10 分钟（首屏看一眼） = **总计 1 小时验证完全部三端**。

---

## 准备清单

### A. 必备账号（不开就完全跑不动的红线 5 个）

| # | 账号 | 用途 | 费用 | 注册 |
|---|------|------|------|------|
| 1 | **微信小程序 AppID** | 小程序编译入口；模拟器 / 真机调试都要 | 个人版 ¥0 / 企业版 ¥300/yr | [mp.weixin.qq.com](https://mp.weixin.qq.com) → 注册 → 小程序 |
| 2 | **硅基流动 API Key** | RAG / Agent 全部依赖（Sprint 2 已就绪） | 新人送 ~¥14 额度，本地调试够用 | [siliconflow.cn](https://siliconflow.cn) |
| 3 | **HBuilderX**（IDE）| App 端 Android / iOS 打包 + 标准基座真机调试 | 免费 | [dcloud.io/hbuilderx.html](https://www.dcloud.io/hbuilderx.html) |
| 4 | **微信开发者工具** | 小程序唯一调试入口 | 免费 | [开发者工具下载](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html) |
| 5 | **DCloud 账号** | HBuilderX 标准基座 + 云打包 | 免费 | HBuilderX 内首次打开自动引导 |

**按需账号**：
- iOS 真机调试：Apple Developer Program $99 / yr（仅真机 / TestFlight 才必要；模拟器免费）
- Android 真机调试：无需任何付费账号

### B. 本机工具链（macOS）

```bash
brew install --cask docker                       # PG / Redis 跑容器里
brew install node@20 pnpm                        # uniapp 前端依赖
curl -LsSf https://astral.sh/uv/install.sh | sh  # Python uv（FastAPI 依赖管理）
brew install postgresql@16                       # 仅装 psql 客户端（PG 服务跑 docker）
xcode-select --install                           # iOS 模拟器必备（仅 macOS）
brew install --cask android-studio               # 仅 Android 真机时要 ADB；标准基座路径不强制
```

### C. 5 个关键 .env 字段（后端必填）

```bash
cp xgzh/apps/api/.env.example xgzh/apps/api/.env
```

| 字段 | 是否必填 | 取值方法 |
|---|---|---|
| `SILICONFLOW_API_KEY` | 必填 | siliconflow.cn 注册后复制 |
| `JWT_SECRET` | 必填 | `openssl rand -hex 32` 生成 |
| `DATABASE_URL` | 已有默认 | docker-compose 默认 `postgresql+asyncpg://xgzh:xgzh_dev_pass@localhost:5432/xgzh` |
| `REDIS_URL` | 已有默认 | `redis://localhost:6379/0` |
| `WECHAT_MP_APP_ID` / `_SECRET` | 选填 | 留空时小程序登录返回 503，开发用 OTP Tab 走 mock SMS 兜底 |

---

## 本地三端跑通

### Phase 1：5 分钟跑 H5（最快）

> 这条路径**完全绕过**小程序 / Native 账号体系，纯浏览器跑，最适合先看一眼"长啥样"。

#### 1. 起后端基础设施

```bash
cd xgzh/infra
docker compose up -d postgres redis      # meilisearch 不必起，Sprint 4 才用
docker compose ps                         # 看到 xgzh-postgres + xgzh-redis 都 healthy
```

#### 2. 起后端 API

```bash
cd xgzh/apps/api
cp .env.example .env
# 编辑 .env, 至少填:
#   SILICONFLOW_API_KEY=sk-xxx
#   JWT_SECRET=$(openssl rand -hex 32 输出)

uv sync                                              # 装依赖, 1-2 分钟
uv run alembic upgrade head                          # 建 11 张表 + pgvector (~3s)
uv run uvicorn app.main:app --reload --port 8000     # 启动!
# 期望日志: "scheduler.jobs_registered" + "scheduler.started"
# 5 秒后 scheduler 会自动跑一次 A 股 ingest, 表里 ~200 条 IPO
```

**冒烟验证**（开新终端）：

```bash
curl http://localhost:8000/healthz                   # → {"status":"ok"}
curl 'http://localhost:8000/api/v1/ipos?market=A&size=3' | jq
```

#### 3. 起前端 H5

```bash
cd xgzh/apps/mp
pnpm install                                         # ⚠️ 见 §坑 1
pnpm dev:h5
# 自动开 http://localhost:5173 (manifest.json 已配 vite proxy 转发 /api → :8000)
```

**手机预览**：电脑 + 手机连同一 Wi-Fi → 浏览器输 `http://<电脑内网 IP>:5173`（macOS 看 IP：`ipconfig getifaddr en0`）。

---

### Phase 2：15 分钟跑微信小程序

#### 1. 拿 AppID（一次性）

[mp.weixin.qq.com](https://mp.weixin.qq.com) 登录 → 开发管理 → 开发设置 → **AppID(小程序ID)**，复制。

#### 2. 填到 manifest

编辑 `xgzh/apps/mp/manifest.json`：

```json
"mp-weixin": {
    "appid": "wx你的appid",
    ...
}
```

#### 3. 编译

```bash
cd xgzh/apps/mp
pnpm dev:mp-weixin         # 或 HBuilderX 工具栏 "运行 → 运行到小程序模拟器 → 微信开发者工具"
# 产物在 dist/dev/mp-weixin/
```

#### 4. 打开调试

微信开发者工具 → **导入项目** → 目录选 `xgzh/apps/mp/dist/dev/mp-weixin/` → AppID 填刚才那个 → **不勾**「使用云开发」。

> 详细配置：项目设置 → **不校验合法域名** + **不校验 https**（仅本地调试用，体验版要关）。这是因为后端跑 `http://localhost:8000` 没 https / 备案。

#### 5. 真机扫预览码（可选）

微信开发者工具 → 工具栏「预览」→ 二维码扫描。**注意**：手机访问 `localhost` 不通，必须改前端 baseURL 为电脑内网 IP（见 §坑 2）。

---

### Phase 3：30 分钟跑 Android 真机

> "标准基座"= DCloud 提供的预编译壳 APK，已装好所有 native 模块；调试时只推 JS 包进基座，不打整包，开发体验**极佳**。

#### 1. 准备真机

- Android 手机打开**开发者模式**：设置 → 关于手机 → 连续点版本号 7 次 → 返回 → 开发者选项 → 开 USB 调试
- USB 数据线连电脑

#### 2. HBuilderX 启动

1. HBuilderX → 文件 → 打开目录 → 选 `xgzh/apps/mp/`
2. 工具栏 **运行 → 运行到手机或模拟器 → 运行到 Android App 基座**
3. 第一次会下载标准基座 APK（约 50MB，自动装到手机），等 1-2 分钟
4. 启动后手机自动打开 App，看到首页

#### 3. 配 baseURL（关键）

Android 上 `localhost` 指手机自己，**必须改成电脑内网 IP**：

```typescript
// xgzh/apps/mp/utils/request.ts:14
const DEFAULT_BASE_URL = 'http://192.168.x.x:8000'   // 你的电脑 IP
// xgzh/apps/mp/utils/sse.ts:33
const DEFAULT_BASE_URL = 'http://192.168.x.x:8000'   // 同步改
```

> 改完 HBuilderX 自动热重载，手机端会自动刷新（DCloud 标准基座支持 HMR）。

---

### Phase 4：30 分钟跑 iOS 模拟器

> macOS 必备；Windows / Linux 跑不了（苹果限制）。**不需要 $99 开发者账号**。

#### 1. 准备 Xcode 模拟器

```bash
xcode-select --install        # 第一次装很慢，~30 分钟
xcrun simctl list devices available    # 验证模拟器列表
```

#### 2. HBuilderX 跑到 iOS 模拟器

1. HBuilderX → 工具栏 **运行 → 运行到手机或模拟器 → 运行到 iOS 模拟器**
2. 第一次下载 iOS 标准基座（约 100MB）+ 启动 Simulator.app
3. App 启动后看到首页

#### 3. iOS 模拟器的 baseURL

iOS 模拟器**实际跑在 Mac 上**，所以 `http://localhost:8000` 是通的（与 H5 同行为），**不需要改 IP**。

#### 4. iOS 真机（可选，需 $99）

- 必须 Apple Developer Program ($99/yr)
- HBuilderX → 发行 → 原生 App-云打包 → iOS → 走 DCloud 苹果证书代生成流程
- **不推荐现在花这 $99**，等 Sprint 4 临近 TestFlight 时再说

---

### 三端调试对照表

| 端 | 启动命令 / IDE | 后端地址 | 登录可走 | 调试工具 |
|---|---|---|---|---|
| **H5** | `pnpm dev:h5` | 相对路径 + vite proxy | OTP / 微信(实际跑不通,跳过) | Chrome DevTools |
| **小程序模拟器** | 微信开发者工具导 `dist/dev/mp-weixin` | `localhost:8000`（关合法域名校验） | OTP / 微信 MP（需 BE 配 secret）| 微信开发者工具 |
| **小程序真机预览** | 同上 + 扫预览码 | 必须电脑内网 IP | 同上 | 远程调试 panel |
| **Android 标准基座** | HBuilderX 运行到 Android App 基座 | 必须电脑内网 IP | OTP（微信端要走 SDK，跳过）| HBuilderX 控制台 |
| **iOS 模拟器** | HBuilderX 运行到 iOS 模拟器 | `localhost:8000` ✅ | OTP（同上）| HBuilderX 控制台 |
| **iOS 真机** | HBuilderX 离线打包 + Xcode | 电脑内网 IP / 公网 | 同上 | Xcode |

---

## 部署方案 A · 免费版（内测期 / 0-100 DAU）

### 设计目标

让 50-100 个内测用户跑得动，年总成本 ≈ **¥30-200**（仅 LLM 调用费 + 可选域名）。**vibe coding 原则：没用户 / 没数据时别花钱**。

### 推荐组合（⭐⭐⭐⭐⭐）

| 组件 | 选择 | 容量 | 限制 |
|---|---|---|---|
| **服务器** | **Oracle Cloud Free Tier** ARM 永久免费 | 4 OCPU + 24 GB RAM + 200 GB 块存储 | 海外节点（首尔 / 东京 / 大阪），需翻墙注册 |
| **数据库** | **Supabase Free**（自带 pgvector）| 500 MB 存储 + 50K 月活 + 无限请求 | 7 天无活动暂停（cron 心跳即可） |
| **缓存** | **Upstash Redis Free** | 256 MB + 10K cmd / 天 | 够 MVP，超了改自建 |
| **对象存储** | **Cloudflare R2 Free** | 10 GB 存储 + 100 万 Class A 操作 / 月 | 招股书 PDF / 海报放这 |
| **LLM** | 硅基流动新人 ¥14 + 按需充值 | 跑 1000 次 Agent ≈ ¥30-50 | 唯一不可避免的开销 |
| **域名** | Cloudflare 免费 `*.workers.dev` 子域 / [is-a.dev](https://is-a.dev) 免费二级域 | 内测期足够 | 不能用于小程序合法域名（要 https + 备案）|
| **CDN + DNS** | Cloudflare Free | 无限带宽 | 内置 SSL |
| **监控** | UptimeRobot Free + Sentry Free | 50 monitors + 5K errors / 月 | 够内测 |
| **iOS** | 模拟器 + 个人证书自签 7 天有效 | 仅自己 / 朋友手动安装 | 没法 TestFlight |
| **Android** | **蒲公英内测分发** + 自签 APK | 不限下载 | 不上架应用市场 |
| **小程序** | **个人小程序**注册免费 | 上线发布免费 | **不能开微信支付** |

**年总成本估算**：¥30-200（仅 LLM + 可选 Namecheap 域名 ¥7）

### 部署步骤（约 4-6 小时）

#### Step 1：申 Oracle Cloud Free Tier（约 30 分钟，**最难一步**）

- 注册：[oracle.com/cloud/free](https://www.oracle.com/cloud/free)
- 需 Visa 信用卡验证（**不会扣款**，hold $1 后退）
- Region 选 **Tokyo (ap-tokyo-1)** 或 **Seoul** 延迟低
- 创建 ARM 实例：Shape `VM.Standard.A1.Flex`，4 OCPU + 24 GB RAM
- OS 选 `Ubuntu 22.04`，SSH key 上传后能登入

> ⚠️ **抢不到怎么办**：Oracle 免费实例 1-2 年很难抢；反复 "Out of capacity" 就换 Region 再试。实在抢不到 → 退而求其次 **Fly.io Free**（3 个 256 MB 实例 + 3 GB 存储，足够 MVP API 单实例）。

#### Step 2：装 Docker + 起后端（约 1 小时）

```bash
# 在 Oracle ARM 实例上
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER && newgrp docker

git clone <你的仓库> && cd xgzh
cd infra && docker compose up -d postgres redis    # ⚠️ 改 docker-compose 仅暴露 127.0.0.1
cd ../apps/api
cp .env.example .env
# .env 里改:
#   APP_ENV=prod
#   DATABASE_URL=postgresql+asyncpg://...      # 见下文 Supabase 决策
#   REDIS_URL=redis://default:xxx@us1-xxx.upstash.io:6379
#   SILICONFLOW_API_KEY=sk-xxx
#   JWT_SECRET=$(openssl rand -hex 32)
#   CORS_ORIGINS=https://你的域名

uv sync --frozen
uv run alembic upgrade head
# 用 systemd 拉起 uvicorn (生产不用 --reload)
```

> ⚠️ **决策点：用 Supabase 替代 docker postgres？**
>
> | 选 | 优点 | 缺点 |
> |---|---|---|
> | **Supabase Free** | 不消耗 Oracle 内存，省运维 | 跨网络 RTT 高（Oracle 东京 → Supabase us-east ~150 ms）|
> | **本机 docker pgvector** | RTT < 1 ms 性能好 | 备份要自己做，崩了要自己救 |
>
> **推荐**：本机 pgvector + 每天 cron `pg_dump` 一份到 Cloudflare R2。

#### Step 3：装 Caddy（自动 https）+ 反向代理（约 30 分钟）

```bash
sudo apt install caddy
sudo tee /etc/caddy/Caddyfile <<EOF
api.你的域名.com {
    reverse_proxy localhost:8000
    encode gzip
}
EOF
sudo systemctl reload caddy
```

#### Step 4：拿域名（5 分钟）

- **不想花钱**：Cloudflare 免费 `*.workers.dev` 子域，或 [is-a.dev](https://is-a.dev) 免费给开源项目分配二级域
- **想花一点点**：Namecheap `.xyz` 域名 ~¥7 / 年（**注意**：不能在国内备案，仅用于海外用户 / Android / iOS / H5；微信小程序合法域名要求**国内备案**的 .com / .cn）

#### Step 5：前端打包 + 部署（约 1 小时）

**H5 → Cloudflare Pages**：

```bash
cd xgzh/apps/mp
# 先把 utils/request.ts + utils/sse.ts 的 DEFAULT_BASE_URL 改成 https://api.你的域名.com
pnpm build:h5
# 上传 dist/build/h5/ 到 Cloudflare Pages，提供 *.pages.dev 免费 https
```

**Android APK → 蒲公英**：

```bash
# HBuilderX → 发行 → 原生 App-云打包 → Android → 自签证书 → 拿 .apk
curl -F file=@xgzh.apk -F _api_key=<你的key> https://www.pgyer.com/apiv2/app/upload
# 拿到下载短链, 发给内测用户
```

**iOS**：模拟器够用就先跳过；想给朋友装 → Xcode 自签 7 天证书；想 TestFlight → 升级付费版。

**小程序**：因为没备案域名，先放弃公测；仅"开发版"（自己 + 团队 < 15 个白名单成员能扫码）。

#### Step 6：监控（10 分钟）

- [UptimeRobot](https://uptimerobot.com)：1 条监控 `https://api.你的域名.com/healthz`，5 分钟一次，挂了发邮件
- 后端加 Sentry SDK（`pip install sentry-sdk`），免费 5K errors / 月

### 免费方案的硬性限制

| 限制 | 影响 | 解法 |
|---|---|---|
| 微信小程序无法开支付 | VIP 收钱完全不通 | 内测期反正不收钱，正式收钱时升级付费版 |
| iOS 不能 TestFlight | 只能朋友圈手动签发 7 天证书 | $99 / yr 免不了，付费版 |
| 域名不备案 → 小程序合法域名校验过不了 | 小程序正式版上不了线 | 备案要 .com / .cn 域名 + ICP 主体（个人 / 企业），见付费版 |
| Oracle Free Tier 海外节点 | 国内用户首字节延迟 ~150 ms | 接 Cloudflare CDN 缓解 80%；或忍着；正式上线切国内 |
| 短信 OTP 真发 | 阿里云 ¥0.045 / 条起步 | 内测继续 mock SMS，前端粘贴验证码 |

---

## 部署方案 B · 付费版（正式上线 / 1K-5K DAU）

### 设计目标

能稳定撑 5K DAU，年总成本 **¥6K-60K**（强相关 DAU）。

**关键决策**：CN 用户优先 → 走**国内 ICP 备案** + **腾讯云 / 阿里云**国内节点。香港 / 海外用户用 Cloudflare CDN 加速。

### 推荐组合 — "国内合规闭环"

| 组件 | 选择 | 配置 | 单价 | 年成本 |
|---|---|---|---|---|
| **域名 + 备案** | 阿里云 `.com` 域名 | 1 个 | ¥69 / 年 | ¥69 |
| **ICP 备案** | 阿里云免费协助 | 个人或企业主体 | ¥0（提交资料 2-4 周通过）| ¥0 |
| **服务器**（API 主战）| **腾讯云轻量 SVS 2C 4G**（CN 上海） | 60 GB SSD + 6 Mbps 带宽 + 1.2T 月流量 | ¥48-80 / 月 | ¥600-1000 |
| **数据库（PG）** | 自建 docker pgvector 在 ECS 上 | — | ¥0 | ¥0 |
| **数据库 → 升级版**（DAU > 1K 后） | 阿里云 RDS PostgreSQL 标准版 1C 2G | 50 GB SSD | ¥150 / 月 | ¥1800 |
| **Redis** | 自建 docker redis（同 ECS） | — | ¥0 | ¥0 |
| **对象存储** | 腾讯云 COS 标准存储 | 50 GB + CDN | ¥6 / GB / 年 + 流量 ¥0.18 / GB | ¥300-500 |
| **CDN** | 腾讯云 CDN | 1 TB / 月 | ¥0.18 / GB | ¥200-500 |
| **短信 OTP** | 阿里云短信国内 | 1 万条 / 月 | ¥0.045 / 条 | ¥540 |
| **LLM 调用** | DeepSeek 直连 + 硅基流动备份 | 5K DAU × 5 次 / 天 = 75 万次 / 月，单次 ~¥0.05 | — | ¥45000 ⚠️ |
| **微信支付商户号** | 企业资质必备 | 一次性认证 ¥300 + 0.6% 抽成 | ¥300 | ¥300 |
| **微信小程序企业认证** | 注册主体必需 | ¥300 / 年 | ¥300 | ¥300 |
| **Apple Developer** | iOS 上架 + IAP | $99 / yr | $99 | ¥720 |
| **Google Play** | Android 上架 | $25 一次性 | $25 | ¥180（首年）|
| **国内 Android 应用市场** | vivo / OPPO / 小米 / 华为 | 个人开发者免费上架（要营业执照）| ¥0 | ¥0 |
| **监控告警** | 阿里云 ARMS Free + 自建 Grafana | — | ¥0-50 / 月 | ¥0-600 |
| **Sentry 错误追踪** | Sentry Pro | ¥0 ~ ¥260 / 月（看量）| ¥0-3000 | ¥0-3000 |

#### 年总成本汇总（按 DAU 拆档）

| 项 | DAU 500（启动）| DAU 2000（爬坡）| DAU 5000（封顶）|
|---|---:|---:|---:|
| 服务器 + DB | ¥600 | **¥2400**（升 RDS）| ¥6000 |
| 域名 + 备案 + 小程序认证 | ¥369 | ¥369 | ¥369 |
| iOS + Android（首年）| ¥900 | ¥720（次年起，不再有 Google 25 美元）| ¥720 |
| 短信 + 存储 + CDN | ¥1000 | ¥1300 | ¥3000 |
| LLM | **¥3000** | **¥15000** | **¥45000** |
| 监控 | ¥0 | ¥600 | ¥3600 |
| **合计** | **~¥6000** | **~¥21000** | **~¥60000** |

> **关键洞察**：DAU < 1K 时 LLM 占比最大；DAU > 1K 后服务器 + DB 上升成大头。spec/06 §4 业务目标：12 个月 ARPU ¥80，5K DAU = 年收入 ¥40 万，毛利率 85%（远高于成本封顶 ¥6 万）。

### 部署步骤（约 1-2 周，瓶颈在 ICP 备案）

#### Phase 1：账号 + 资质（同时进行，2 周完成）

| 任务 | 时长 | 注意 |
|---|---|---|
| 阿里云域名 → 备案 | 2-4 周 | 个人 / 企业主体；备案期间网站不可访问；提交资料后 8 小时内审核回复 |
| 微信小程序企业认证 | 1-2 天 | **必须企业资质（营业执照）**；个人小程序不能开支付 |
| 微信支付商户号申请 | 1-2 周 | 需小程序认证完 + 营业执照 + 法人身份证 + 银行对公账户 |
| Apple Developer 注册 | 1-3 天 | 个人 $99 / yr；企业 $299 / yr 但需邓白氏码 + 100+ 员工 |
| Google Play 注册 | 即时 | $25 一次性 |
| 阿里云短信签名 + 模板申请 | 1-3 天 | 签名要营业执照证明 |

> **强烈建议**：**第一周一上来买域名 + 提交备案**，后面 2 周做开发，正好等备案下来。

#### Phase 2：服务器部署（备案期间做，约 1-2 天）

```bash
# 腾讯云轻量 / 阿里云 ECS 装 Ubuntu 22.04
curl -fsSL https://get.docker.com | sh

# 拉代码
git clone <你的仓库> && cd xgzh

# 起基础设施
cd infra && docker compose up -d postgres redis

# 起 API（生产用 gunicorn + uvicorn workers, 不要 --reload）
cd ../apps/api
cp .env.example .env
# 改 .env 关键字段:
#   APP_ENV=prod
#   LOG_LEVEL=INFO
#   CORS_ORIGINS=https://www.你的域名.com,https://m.你的域名.com
#   SILICONFLOW_API_KEY=sk-xxx
#   DEEPSEEK_API_KEY=sk-xxx        # 备份
#   JWT_SECRET=$(openssl rand -hex 32)
#   WECHAT_MP_APP_ID=wx你的appid
#   WECHAT_MP_APP_SECRET=xxx
#   ALIYUN_SMS_*                   # 短信凭据
#   SMS_ADAPTER=aliyun

uv sync --frozen
uv run alembic upgrade head
uv run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000

# 写 systemd unit 让它开机自启 + 崩了自动重启
sudo tee /etc/systemd/system/xgzh-api.service <<EOF
[Unit]
After=network.target docker.service
[Service]
WorkingDirectory=/path/to/xgzh/apps/api
EnvironmentFile=/path/to/xgzh/apps/api/.env
ExecStart=/home/ubuntu/.local/bin/uv run gunicorn app.main:app -w 4 -k uvicorn.workers.UvicornWorker -b 127.0.0.1:8000
Restart=always
[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload && sudo systemctl enable --now xgzh-api
```

#### Phase 3：Caddy 反向代理 + https + 限流（约 1 小时）

```caddy
api.你的域名.com {
    reverse_proxy 127.0.0.1:8000
    encode gzip
    rate_limit {
        zone agent_diagnose {
            key {remote_host}
            events 10
            window 1m
        }
    }
    log {
        output file /var/log/caddy/api.log
        format json
    }
}
```

#### Phase 4：备案下来后，配小程序合法域名（约 30 分钟）

[mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发管理 → 开发设置 → **服务器域名**：
- request 合法域名：`https://api.你的域名.com`
- socket 合法域名：`wss://api.你的域名.com`（如果将来上 ws）
- uploadFile / downloadFile：按需

#### Phase 5：三端打包发布（约 2-3 天）

| 端 | 操作 | 渠道 |
|---|---|---|
| **小程序** | `pnpm build:mp-weixin` → 微信开发者工具上传 → 体验版（白名单测试）→ 提交审核（约 1-3 天）| 微信小程序 |
| **H5** | `pnpm build:h5` → 上传腾讯云 COS / 阿里云 OSS → 配 CDN | `https://www.你的域名.com` |
| **Android** | HBuilderX 云打包（DCloud 收 ¥3 / 次或包月）→ 4 大应用市场（华为 / 小米 / OPPO / vivo + 应用宝）+ Google Play | 7 大渠道 |
| **iOS** | HBuilderX 离线打包 → Xcode 上传 → TestFlight 内测 → App Store 审核（约 3-7 天） | App Store |

#### Phase 6：监控告警（约半天）

- 阿里云 ARMS：API 接入应用监控
- Sentry：前后端 error 上报
- UptimeRobot：5 分钟一次的 healthz 心跳
- Grafana + Prometheus：自建在 ECS 上看 PG / Redis 指标

---

## 关键决策清单

> 这些决策会影响多个组件。**Sprint 3 启动前必须想清楚**。

### 决策 1：选哪家服务器（付费版）？

| 选 | 何时 | 理由 | 缺点 |
|---|---|---|---|
| **腾讯云轻量上海** | 起步 / DAU < 1K | 微信生态原生，备案最顺，¥48 / 月入门 | 流量超额贵 |
| **阿里云 ECS 突发性能** | DAU 上千后升 | 弹性带宽 + RDS 集群 | 成本上去快 |
| 阿里云 / 腾讯云**香港轻量** | 不想备案 | 小程序也能用（但要走 H5 webview）| 国内访问延迟高 50-100 ms |

→ **推荐起步：腾讯云轻量上海 ¥48 / 月**；DAU 破 1K 升 ECS + RDS。

### 决策 2：DB 自建 vs 托管？

| 选 | 何时切换 |
|---|---|
| **自建 docker pgvector** + 每日 `pg_dump` 到 OSS | DAU < 1000 用这个 |
| **阿里云 RDS PostgreSQL**（带 pgvector 插件）| DAU > 1000 切换；省心可靠 |

→ **推荐起步自建**，省 ¥1800 / 年；DAU 上来再迁。

### 决策 3：小程序企业认证 vs 个人？

| 项 | 个人 | 企业 |
|---|---|---|
| 注册费 | ¥0 / 年 | ¥300 / 年 |
| 微信支付 | ❌ 不能开 | ✅ 可开 |
| 用户上传图片 | ❌ 不能挂 | ✅ 可挂 |
| 类目限制 | 金融严格禁止 | 全类目齐全 |

→ 你的产品是金融类，**必须企业认证**，不绕开。

### 决策 4：iOS / Android 上架顺序？

| 阶段 | 顺序 | 理由 |
|---|---|---|
| 内测期（MVP）| Android 蒲公英 + iOS 模拟器 / 朋友圈测试 | 不花钱看反馈 |
| 公测期（小范围放量）| 小程序体验版 + Android Google Play / 蒲公英 | 微信小程序最快出量 |
| 正式期 | Android 国内 4 大市场 + iOS App Store + 小程序正式版 | iOS 审核最慢，提前 2 周准备 |

→ spec/07 §S5 排期"微信小程序提审 + Android Beta + iOS TestFlight"是正解。

### 决策 5：你应该选 A 还是 B？

| 你的当前阶段 | 推荐 | 理由 |
|---|---|---|
| 「先看眼 UI / 体验，确认审美」 | §Phase 1 H5 | 5 分钟跑通，¥0 成本，最快反馈 |
| 「给朋友 / 内测用户用，没收入」 | **方案 A 免费版** | 1 个月内 ¥30-100 LLM 费用就够 |
| 「准备拉真实用户 / 收钱了」 | **方案 B 付费版** | 必须备案 + 企业资质 + 微信支付，没有 quick win |

---

## 已知坑速查表

### 坑 1：`pnpm install` 报 `@dcloudio/uni-h5@3.0.0-4060920241225001` not found

> 上游 npm 包被 yank 的历史问题，spec/09 多处提及。

**解法 3 选 1**：

| 方案 | 操作 | 风险 |
|---|---|---|
| **A. 改用 HBuilderX 内置编译**（推荐）| HBuilderX 打开 `apps/mp` → 运行到浏览器（不走 pnpm）| 不能用 vite 热更，但版本最稳定 |
| B. 锁版本到上一个稳定 tag | `package.json` 5 个 `@dcloudio/*` 改 `3.0.0-4030720241128003` 或 `^3.0.0` | 可能要小改 import |
| C. `--frozen-lockfile=false --strict-peer-dependencies=false` | 让 pnpm 自动找替代版本 | 可能跑出来报别的错 |

→ **推荐 A**（HBuilderX 路径），与 Phase 3 / 4 App 调试统一同一套 IDE，少装一个工具链。

### 坑 2：手机 / 真机 / Android 访问 `localhost:8000` 不通

| 场景 | 后端地址 | 改哪 |
|---|---|---|
| 开发者工具模拟器 | `http://localhost:8000` ✅ 直接通 | 不用改 |
| 真机扫预览码（小程序）| 必须电脑内网 IP | 改 `apps/mp/utils/request.ts` + `utils/sse.ts` 的 `DEFAULT_BASE_URL = 'http://192.168.x.x:8000'` |
| Android 真机 | 同上 | 同上 |
| iOS 模拟器（在 Mac 上跑）| `http://localhost:8000` ✅ | 不用改 |
| iOS 真机 | 必须电脑内网 IP | 同上 |
| 体验版 / 正式版 | 必须 https + 备案域名 | 后端部署后再改 |

> macOS 看内网 IP：`ipconfig getifaddr en0`

**短期解法**：开发者工具 → 项目设置 → **不校验合法域名** + **不校验 https**（仅本地调试用，体验版要关）。

### 坑 3：小程序登录会失败（`/auth/login/wechat-mp` 503）

`pages/auth/login` 走 `POST /auth/login/wechat-mp`，需后端 `.env` 填 `WECHAT_MP_APP_ID` + `WECHAT_MP_APP_SECRET`。**没填则 503**。

**绕开**：登录页有"手机号 OTP"另一个 Tab，开发期 `SMS_ADAPTER=mock`（默认就是），后端日志里直接打印 OTP 验证码，复制粘贴登录即可。

### 坑 4：Oracle Cloud Free Tier "Out of capacity"

最近 1-2 年很难抢；反复换 Region 试。实在抢不到 → 退而求其次 **Fly.io Free**（3 个 256 MB 实例 + 3 GB 存储，足够 MVP API 单实例）。

### 坑 5：阿里云 ICP 备案不能跳过（小程序合法域名硬要求）

- 提交后 8 小时内电话核验，错过当天作废
- 备案期间网站不可访问
- 个人备案：身份证 + 手持照 + 域名证书 + 服务器购买凭证
- 企业备案：营业执照 + 法人身份证 + 公章

→ **越早提交越好**，2-4 周下来；DOR 已写在 spec/10 Sprint 3 启动前清单。

---

## 附录

### A. .env 字段速查

详见 [`apps/api/.env.example`](../apps/api/.env.example)，关键字段已在 §准备清单 §C 列出。

### B. 资质 / 账号办理时长一览

| 资质 | 时长 | 关键路径 |
|---|---|---|
| 微信小程序个人版 | 即时 | 个人身份证 |
| 微信小程序企业版 | 1-2 天 | 营业执照 + ¥300 / 年认证 |
| 微信支付商户号 | 1-2 周 | 小程序认证完 + 法人身份证 + 银行对公账户 |
| ICP 备案（个人）| 2-3 周 | 域名 + 服务器购买凭证 + 身份证手持照 |
| ICP 备案（企业）| 2-4 周 | 营业执照 + 法人身份证 + 公章 |
| Apple Developer | 1-3 天 | $99 / yr 信用卡 |
| Google Play | 即时 | $25 一次性 |
| 阿里云短信签名 | 1-3 天 | 营业执照证明 |

### C. 域名选购参考

| 后缀 | 价格 | 备案 | 用途 |
|---|---|---|---|
| `.com` | ¥69 / 年（阿里云）| ✅ 可备案 | 主推（小程序合法域名要求）|
| `.cn` | ¥39 / 年（阿里云）| ✅ 可备案 | 备选 |
| `.xyz` | ¥7 / 年（Namecheap）| ❌ 不能备案 | 仅海外用户 / H5 / Android / iOS |
| `*.workers.dev` | 免费 | — | 内测期 H5 |
| `*.pages.dev` | 免费 | — | 内测期 H5（Cloudflare Pages）|

### D. 文档 + spec 索引

- [README.md](../README.md) — 项目总览 + Sprint 进度
- [AGENTS.md](../AGENTS.md) — AI 助手最高铁律
- [spec/06](../spec/06-商业化变现与合规避险.md) — Freemium / CPA / 法律隔离
- [spec/07](../spec/07-MVP开发清单与排期.md) — MVP 10-12 周排期
- [spec/08](../spec/08-sprint-1-backlog.md) — Sprint 1 backlog（用户 + IPO + 自选）
- [spec/09](../spec/09-sprint-2-backlog.md) — Sprint 2 backlog（AI Agent + RAG）
- [spec/10](../spec/10-sprint-3-backlog.md) — Sprint 3 backlog（文章 + 券商 + VIP 订阅）

---

> **维护者注**：本文档随项目演进而更新。每跑通一次新场景或踩到新坑，回填到对应 §坑表；账号申请流程变更时更新 §准备清单。Sprint 3 部署完成后，§部署方案 B Phase 5 添加你实际用的域名 / CDN / 备案号占位。
