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

#### 1. 起后端基础设施 (PG + Redis)

**两条路**, 二选一:

```bash
# 路 A (推荐): 用 docker (跨平台, 一键起)
cd xgzh/infra
docker compose up -d postgres redis      # meilisearch 不必起, Sprint 4 才用
docker compose ps                         # 看到 xgzh-postgres + xgzh-redis 都 healthy

# 路 B: 复用本地 Homebrew PG / Redis (macOS 上常见)
# 前提: 已 brew install postgresql@14 + redis 且 brew services 已起
# 验证 pgvector 可用 + xgzh 库 + 用户已在:
psql postgres -c "SELECT * FROM pg_available_extensions WHERE name='vector';"  # → 0.8.2
psql -d xgzh -c "\dt" 2>&1 | head -20                                          # → 11 张表 (走 alembic 后)
# 已经跑过 alembic 的 dev 环境直接用, 不用再 docker
```

#### 2. 起后端 API

```bash
cd xgzh/apps/api
cp .env.example .env
# 编辑 .env, 至少填一个 LLM key + JWT_SECRET:
#   ZHIPU_API_KEY=xxx                      # 推荐: GLM-4-Flash 永久免费 (申请: bigmodel.cn)
#   # 或 SILICONFLOW_API_KEY=sk-xxx        # 新人 ¥14 额度
#   # 或 DEEPSEEK_API_KEY=sk-xxx           # 新人 ¥10 额度
#   LLM_PRIMARY_MODEL=zhipu/glm-4-flash    # 跟 KEY 对齐
#   LLM_FALLBACK_MODEL=zhipu/glm-4-flash
#   JWT_SECRET=$(openssl rand -hex 32 输出)

uv sync                                              # 装依赖, 1-2 分钟
uv run alembic upgrade head                          # 建 11 张表 + pgvector (~3s)
uv run uvicorn app.main:app --reload --port 8000     # 启动!
# 期望日志: "Application startup complete" + 6 个 tool_registry.register
# 5 秒后 scheduler 会自动跑一次 A 股 ingest, 表里 ~200 条 IPO
```

> **2026-04-26 实战修复**: `zhipu/` 前缀 LiteLLM 不识别, 已在 `app/adapters/llm_client.py` patch 走 OpenAI 兼容协议 (智谱 paas-v4 endpoint), 见 §坑 9。

**冒烟验证**（开新终端）：

```bash
curl http://localhost:8000/healthz                   # → {"status":"ok"}
curl 'http://localhost:8000/api/v1/ipos?market=A&size=3' | jq
```

#### 3. 起前端 H5

> **2026-04-26 实战已跑通**: 已踩 4 个坑 (§坑 1 / 6 / 7 / 8 / 10), 下面是已验证的最终命令。

```bash
cd xgzh/apps/mp

# (一次性) 切淘宝镜像 (国内访问 npmjs.org 卡死, 见 §坑 10)
pnpm config set registry https://registry.npmmirror.com

# (一次性) 装依赖 (~4 分钟, ~840 个包)
pnpm install --reporter=append-only

# (一次性) 项目根需要 index.html 入口 (新版 vite-plugin-uni 不再自动注入, 见 §坑 7)
# 已在仓库 apps/mp/index.html 落地, 不需要再手动建; 全新克隆时确认这个文件存在

# 起 dev server (UNI_INPUT_DIR=. 让新版 plugin 把根目录当 src, 见 §坑 6)
UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist pnpm dev:h5

# 输出会有:
#   vite v5.4.21 dev server running at:
#   ➜ Local: http://localhost:5173/
#   ready in 1390ms
```

**浏览器打开**: `http://localhost:5173/` （**用 `localhost`, 不要 `127.0.0.1`** —— vite 默认监听 IPv6 `[::1]`, 见 §坑 8）。

**冒烟验证**（开新终端）：

```bash
curl -I http://localhost:5173/                                  # → 200 OK + text/html
curl 'http://localhost:5173/api/v1/ipos?market=HK&size=2' | jq  # 走 vite proxy
# 期望看到 "地平线机器人-W" 等港股 IPO
```

**手机预览**: 电脑 + 手机连同一 Wi-Fi → 起 dev server 时改成 `pnpm dev:h5 --host 0.0.0.0` → 浏览器输 `http://<电脑内网 IP>:5173`（macOS 看 IP: `ipconfig getifaddr en0`）。

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

### 坑 1：`pnpm install` 报 `@dcloudio/uni-h5@3.0.0-4060920241225001` not found ✅ 实战已修

> 上游 npm 包被 yank 的历史问题（dcloudio 频繁发布滚动 build tag, 老 tag 经常被回收）。
> **2026-04-26 实战修复**：项目原 pin 的 `3.0.0-4060920241225001` 已不在 npm registry, 必须切到 dcloudio 的 ``vue3`` dist-tag 当前 head。

**实战已落地的修复方案**：

```bash
# 1. 查 vue3 dist-tag 最新 head (dcloudio 同步发布, 9 个包是同一个版本号)
npm view @dcloudio/uni-h5 dist-tags
# 看 vue3 字段, 例 2026-04: 3.0.0-alpha-5000820260420001

# 2. 把 package.json 里 9 个 @dcloudio/* 全部从老 pin 改到这个 head
#    (uni-app / uni-app-plus / uni-components / uni-h5 / uni-mp-weixin
#     uni-automator / uni-cli-shared / uni-stacktracey / vite-plugin-uni)

# 3. 重跑 install
pnpm install --reporter=append-only
```

**3 个 peer warn 可忽略**（`vue 3.4→3.5` / `vite 5.2→5.4` / `types 3.4.30→3.4.31`，全是 patch/minor bump 兼容）。

> **不推荐**：HBuilderX 内置编译路径（与 vite 主线脱节, 后续维护代价高）。

**未来再次撞 yank 时怎么办**：dcloudio 每隔几个月就 yank 一次老 tag, 重做上面 3 步即可（包列表见 [Sprint 9 backlog](../spec/09-sprint-2-backlog.md) 「FE-S2-000 frontend bootstrap」）。

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

### 坑 6：新版 vite-plugin-uni 启动报 `ENOENT: src/manifest.json` ✅ 实战已修

> dcloudio 在 alpha vue3 通道把项目结构默认从「扁平根目录」改成「`src/` 子目录」，但本项目仍是扁平结构（main.ts / App.vue / pages.json / manifest.json 都在 `apps/mp/` 根）。

**症状**：

```
Error: ENOENT: no such file or directory,
open '/.../apps/mp/src/manifest.json'
    at parseManifestJson (.../@dcloudio/uni-cli-shared/dist/json/manifest.js:20:47)
```

**解法**：用 `UNI_INPUT_DIR=.` 告诉 vite-plugin-uni 拿当前目录作为 src 根：

```bash
cd xgzh/apps/mp
UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist pnpm dev:h5
```

**永久化**（避免每次手敲 env）：在 `apps/mp/package.json` 的 `scripts` 里改：

```json
"dev:h5": "UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist uni",
"dev:mp-weixin": "UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist uni -p mp-weixin",
"build:h5": "UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist uni build",
"build:mp-weixin": "UNI_INPUT_DIR=. UNI_OUTPUT_DIR=./dist uni build -p mp-weixin"
```

### 坑 7：H5 启动后 `localhost:5173/` 返回 404 (空响应) ✅ 实战已修

> 新版 vite-plugin-uni alpha 版**不再自动注入内存 index.html**, 项目必须自带入口模板。
> 旧版 (`3.0.0-4060920241225001`) 是内置生成的, 老项目模板里没 index.html 是正常的; 升级后立刻撞这个坑。

**解法**：在 `apps/mp/index.html` 写一个标准 uniapp H5 入口（已落地）：

```html
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1.0,maximum-scale=1.0,user-scalable=no,viewport-fit=cover" />
  <title>新股智汇</title>
  <script>
    document.addEventListener('DOMContentLoaded', function () {
      document.documentElement.style.fontSize = document.documentElement.clientWidth / 20 + 'px'
    })
  </script>
</head>
<body>
  <div id="app"></div>
  <script type="module" src="/main.ts"></script>
</body>
</html>
```

`vite-plugin-uni` 会在启动时把 `globalThis` polyfill 和 HMR client 注入进去, 启动后就能拿到 200。

### 坑 8：vite 默认监听 IPv6 `[::1]:5173` 不监听 IPv4 → `curl 127.0.0.1` 失败 ✅ 知道就行

> vite 5.x 在 macOS 上 `host` 默认值是 `localhost`, 走 `getaddrinfo` 优先 IPv6, 实际监听到 `[::1]`。

**症状**：浏览器打 `http://localhost:5173` 通; `http://127.0.0.1:5173` Connection refused。

**解法**：

| 场景 | 怎么办 |
|---|---|
| 你自己在 Mac 上调试 | 浏览器输 `http://localhost:5173/` (用 `localhost` 不要 `127.0.0.1`) |
| 同 Wi-Fi 真机 / 手机预览 | `pnpm dev:h5 --host 0.0.0.0` 让 vite 监听全网卡 |
| Docker 容器内访问宿主机 vite | 同上 (`--host 0.0.0.0`) |

### 坑 9：LiteLLM 不认识 `zhipu/<model>` 前缀路由 ✅ 实战已修 (PR 待提)

> `app.adapters.llm_client._build_completion_kwargs` 会把 `zhipu/glm-4-flash` 直接传 LiteLLM, 但 LiteLLM 1.51 没有原生 `zhipu/` provider, 报 `LLM Provider NOT provided`。
> 项目原作者大概率没真测过 zhipu 路径 (Sprint 1/2 都用硅基流动)。

**症状**：

```
LLMProviderError: chat call failed: litellm.BadRequestError:
LLM Provider NOT provided. Pass in the LLM provider you are trying to call.
You passed model=zhipu/glm-4-flash
```

**解法 (已 patch 在 main 分支)**：把 zhipu 路径走 OpenAI 兼容协议 (智谱 paas-v4 endpoint 兼容 OpenAI Chat Completions)。

```python
# app/adapters/llm_client.py _credentials_for_provider
if provider == "zhipu":
    return settings.zhipu_api_key, "https://open.bigmodel.cn/api/paas/v4"

# _build_completion_kwargs 返回 (kwargs, provider, effective_model)
# 把 zhipu/<x> 重写成 openai/<x> 给 LiteLLM, 但对外契约 (cost 表 / chat_token_usage) 仍记 zhipu
if provider == "zhipu":
    effective_model = "openai/" + model.removeprefix("zhipu/")
```

`chat()` / `stream_chat()` / `astream_chat_with_meta()` / `embed()` 4 处对应改 `model=effective_model`。

**完整成本**: GLM-4-Flash 永久免费 (价格表 `_PRICE_CNY_PER_M_TOKENS` 已录 0.0/0.0), Sprint 3 前完全够本地开发。

### 坑 10：H5 浏览器报 "连接服务器超时", 后端日志一堆 `GET /api/auth.ts 404` ✅ 实战已修

> **命名空间冲突**: 项目同时有前端源码目录 `apps/mp/api/*.ts` 和后端 REST 前缀 `/api/v1/*`, 但 vite proxy 配的是 `/api/*` 一刀切, 导致前端 ES 模块加载请求 `/api/auth.ts` 被错误转给后端 (后端不识 .ts 后缀 → 404 → 前端 module 加载失败 → 显示 "连接服务器超时")。
> 旧版 `vite-plugin-uni` 不会把 `apps/mp/api/*.ts` 直接挂在根 URL, 升级到 vue3 alpha 通道后冲突才显现。

**症状**：

```
# 前端报错
连接服务器超时, 点击屏幕重试

# 后端日志
INFO: 127.0.0.1:56146 - "GET /api/ipo.ts HTTP/1.1" 404 Not Found
INFO: 127.0.0.1:56148 - "GET /api/auth.ts HTTP/1.1" 404 Not Found
```

**解法**: `apps/mp/manifest.json` 里的 vite proxy 路径从 `/api` 收窄到 `/api/v1` (精确匹配后端真实 prefix), 同时显式加上 `/healthz`：

```json
"devServer": {
    "port": 5173,
    "https": false,
    "proxy": {
        "/api/v1": {
            "target": "http://localhost:8000",
            "changeOrigin": true
        },
        "/healthz": {
            "target": "http://localhost:8000",
            "changeOrigin": true
        }
    }
}
```

**改完务必重启 vite dev server** (vite 启动时读 manifest 构建代理表, 运行中改不生效)。

**未来加新后端非 `/api/v1` 路由时** (例如 `/admin/*`、`/webhooks/*`), 记得在 proxy 里**追加新 entry**, 不要把 prefix 缩成 `/`（会重蹈覆辙）。

### 坑 11：`pnpm install` 在 `npmjs.org` 上卡死不动 ✅ 实战已修

> `pnpm install` 默认 registry 是 `https://registry.npmjs.org/`，国内访问可能极慢（10+ 分钟还没到 `Progress: resolved`）。

**解法**：切到淘宝镜像（速度快 10 倍以上）：

```bash
pnpm config set registry https://registry.npmmirror.com
pnpm install
# 实测 ~4 分钟装完 ~840 个 dcloudio + Vue 3 体系包
```

**注意**：

- 这是用户全局 config, 改完后所有 pnpm 项目都走淘宝镜像; 想还原: `pnpm config set registry https://registry.npmjs.org/`
- 不要用 `--reporter=default` 配合 `| tail -N`, tail 会缓冲 stdin 直到 EOF, 你看不到实时进度; 用 `--reporter=append-only` 或不接 pipe

### 坑 12：小程序点 IPO 卡片跳详情，URL 变成 `/api/v1/ipos/undefined` ✅ 实战已修

> **现象**：从首页点新股卡片 → 详情页报 404, 控制台:
> ```
> GET http://localhost:8000/api/v1/ipos/undefined 404 (Not Found)
> ```
> 详情页左上角标题区也显示空白（fallback 显示了字面字符串 "undefined"）。
> H5 端表现 OK，**只在小程序里出现**。

**根因**：uni-app + 小程序的事件名冲突坑。

`@tap` 在小程序 wxml 里是 `view` 元素的**原生事件关键字**。当 `IPOCard.vue` 里写：

```vue
<view @tap="$emit('tap', item)">  <!-- 内部: emit 名叫 'tap' -->
```

外层调用方写：

```vue
<IPOCard @tap="openDetail" />     <!-- 外层: 想接 emit 的 'tap' -->
```

**mp-weixin 编译器优先把 `@tap` 解析为监听根 view 的原生 tap 事件**，而不是组件 emit 的 'tap'。结果：
- `openDetail` 收到的不是 emit 出的 `IPOItem`, 而是原生 `TouchEvent` 对象
- `event.code` 是 `undefined`
- `encodeURIComponent(undefined)` = 字面字符串 `"undefined"`
- url 变成 `/pages/ipo/detail?code=undefined&name=undefined` → 后端 fetch `/api/v1/ipos/undefined` → 404

H5 没此问题: H5 下 Vue 编译为标准 DOM 事件, `@tap` 不会被特殊解析。

**解法**: 自定义组件 emit 名一律避开小程序保留事件 (`tap`/`touchstart`/`touchmove`/`longpress`/`change`/`input` 等); 改名 `select` 后无歧义。

```diff
- defineEmits<{ (e: 'tap', item: IPOItem): void }>()
+ defineEmits<{ (e: 'select', item: IPOItem): void }>()

- @tap="$emit('tap', item)"          // IPOCard 内部
+ @tap="$emit('select', item)"

- <IPOCard @tap="openDetail" />      // 调用方
+ <IPOCard @select="openDetail" />
```

**踩坑预防 / 代码审查 checklist**：自定义组件 `defineEmits` 名禁止与小程序 native 事件名重叠（建议用动词如 `select` / `confirm` / `change-status`，避开 `tap` / `click` / `change` / `input`）。

### 坑 13：小程序首页一片白底 + tab 选中态白底白字看不见 ✅ 实战已修

> **现象**: H5 端配色完美（深蓝主题 + 浅色文字），切到小程序就变成 **白底 + 部分元素失色**，最严重 = 顶部 `港股 / A 股` 切换 tab 选中后**白字白底完全看不见**。

**根因**: `:root` 选择器在小程序 wxss 里**不存在**。

项目 `App.vue` 把全局 CSS 变量 (`--color-bg` / `--color-primary` / `--color-text` 等) 全挂在 `:root {}` 里:

```scss
:root {  /* ❌ 小程序 wxss 不认这个 */
  --color-bg: #0b1220;
  --color-primary: #4f8bff;
  --color-text: #e2e8f0;
  ...
}
page {
  background: var(--color-bg);
  color: var(--color-text);
}
```

H5 端 `:root` = HTML 根 → 变量正常注入到所有元素。

小程序端 wxss 的最顶级是 `page`，**没有 `:root` 等价物**, 这段规则编译后**完全失效**:
- `page { background: var(--color-bg) }` → fallback 到 initial → transparent → 微信默认白底
- `.mtab-active { background: var(--color-primary); color: #fff }` → primary 没值 → transparent → 白底, 而 color 写死 #fff → **白字白底完全不可见**
- 那些写了 fallback 的 (如 `var(--color-text, #e2e8f0)`) 还能正常显示 → 视觉上"部分元素失色"

**解法**: CSS 变量同时挂到 `page` (小程序) + `:root` (H5):

```scss
page,
:root {
  --color-bg: #0b1220;
  --color-surface: #131a2c;
  --color-primary: #4f8bff;
  --color-text: #e2e8f0;
  --color-text-muted: #94a3b8;
  ...
}

page {
  background: var(--color-bg);
  color: var(--color-text);
}
```

**踩坑预防**: 跨 H5/MP 项目的全局变量定义**永远写双选择器** (`page, :root`)，不要只写 `:root`；新加全局 CSS 变量时检查 fallback 兜底，避免单端缺失时 UI 灾难。

### 坑 14：本地没短信通道, OTP 收不到怎么登录 ✅ 实战已修

> **现象**: dev 环境用 `SMS_ADAPTER=mock`，所有 OTP 只打印到后端日志, 用户没法主动复制粘贴 OTP, 体验极差; 多个开发者 / QA 每次测试都要 grep 日志拿验证码。

**解法**: 后端加 OTP **dev whitelist** + 固定 OTP 配置 (BE 改动 ~30 行):

1. `Settings` 增 2 个字段 (`app/core/config.py`):
   ```python
   otp_dev_fixed_phones: str = ""          # 逗号分隔白名单
   otp_dev_fixed_code: str = "888888"      # 白名单统一 OTP
   ```

2. `otp_service.send_otp` 加白名单短路 (`app/services/otp_service.py`):
   ```python
   def _is_dev_whitelisted(phone) -> tuple[bool, str | None]:
       # 双重护栏: 仅 app_env != prod 且 sms_adapter == mock 时生效
       # 否则任何配置都不绕过短信发送 (生产安全兜底)
       ...

   async def send_otp(phone, ttl_seconds):
       hit, fixed = _is_dev_whitelisted(phone)
       if hit:
           await store_otp(phone, fixed, ttl_seconds=ttl_seconds)
           logger.warning(f"[DEV-WHITELIST] phone={mask_phone(phone)} code={fixed}")
           return SMSSendResult(provider="dev-whitelist", success=True, ...)
       # 否则走原 mock / aliyun adapter
       ...
   ```

3. `.env` 配置:
   ```bash
   SMS_ADAPTER=mock
   OTP_DEV_FIXED_PHONES=13007458553,15912345678   # 你和队友的号
   OTP_DEV_FIXED_CODE=888888
   ```

**生效条件 (双重护栏, 防止配置被误代到 prod)**:

| `APP_ENV` | `SMS_ADAPTER` | 白名单 |
|---|---|---|
| `dev` | `mock` | ✅ 生效 (打到日志, 无外网) |
| `dev` | `aliyun` | ❌ 不生效 (走真实通道) |
| `prod` | * | ❌ 永不生效 |

**用法**: 配好后, 用白名单手机号 (如 `13007458553`) → `/auth/otp/send` 不发短信但写 Redis → `/auth/login/phone` 用 `888888` 即可登录。后端日志会打 `[DEV-WHITELIST] otp.short_circuit code=888888`。

**踩坑预防**: 别在 prod 误开（双重护栏已挡）; 但要把 `OTP_DEV_FIXED_PHONES` 加进 `.env.example` 注释为 "DEV ONLY", 避免新人 copy 到 prod 配置。

### 坑 15：小程序里 `URLSearchParams is not defined` ✅ 实战已修

> **现象**: 首页加载列表报 "加载失败: URLSearchParams is not defined"。控制台:
> ```
> ReferenceError: URLSearchParams is not defined
> ```
> H5 端正常, App 端正常, **只在小程序里出现**。

**根因**: 微信小程序 JSCore (V8/JSC sandbox) **不暴露 `URLSearchParams` 全局**。同类不兼容的还有: `URL` 构造函数 (大多数版本), `EventSource`, `fetch` (有 wx.request 替代), `window` / `document`, `localStorage` (用 wx.storage), `atob` / `btoa` (3.7+ 才有), `Blob`, `FormData`.

**解法**: 拼 query string 的需求, 用 `uni.request` GET + `data` 字段 (uni-app 跨平台自动序列化):

```diff
- const qs = new URLSearchParams()
- qs.set('market', market)
- qs.set('page', String(params.page ?? 1))
- if (params.status) qs.set('status', params.status)
- return request({ url: `/api/v1/ipos?${qs.toString()}` })

+ const data: Record<string, string | number> = {
+   market,
+   page: params.page ?? 1,
+   size: params.size ?? 20,
+ }
+ if (params.status) data.status = params.status
+ return request({ url: '/api/v1/ipos', data })
```

**全项目预扫脚本** (新写 mp 代码前可跑):

```bash
# 在 apps/mp/ (排除 dist) 找所有可能的不兼容 API
rg "URLSearchParams|new URL\(|EventSource|^fetch\(|window\.|document\.|localStorage|sessionStorage|atob\(|btoa\(|TextEncoder|TextDecoder|Blob\(|FormData\(" \
   apps/mp/ --glob '!**/dist/**'
```

凡是出现的, 检查是否在 `// #ifdef H5` 块里 (H5 才会编译进去, 安全) 或者有 fallback (`typeof X !== 'undefined' ? new X() : ...`); 否则要么删, 要么走 uni-app 跨端 API (`uni.request` / `uni.setStorageSync` / `uni.connectSocket` 等).

**踩坑预防**: 项目根目录加一条 ESLint custom rule (TODO Sprint 3): 禁止在 `apps/mp/` 下未带 `// #ifdef H5` 的位置使用 `URLSearchParams` / `URL` 等浏览器全局; 或者写一个 pre-commit hook 跑上面的 rg 命令并报错.

### 坑 16：港股 IPO 数据空 / hkexnews 返回 503 ⚠️ 已识别, 港股真源接入推迟到 Sprint 3 单独 PR

> **现象**: 首页港股 tab 只有 3 条 cold-start seed 数据 (`地平线机器人-W` / `速腾聚创` / `理想汽车-W`), footer 显示"数据来源: seed"; 后端日志:
> ```
> WARNING hkex.fetch_applicants 4xx status=404 (实际是 503 Akamai blocked, log 输出近似)
> WARNING ipo_ingest.fetch_hk empty (hkexnews returned 0 applicants)
> ```
> A 股 tab 数据 OK (akshare CNINFO 入库 200 条 ✅).

**根因**: 港交所披露易 (hkexnews `https://www1.hkexnews.hk/app/listing/applicants/applicants_c.htm`) 用 **Akamai WAF** 反爬, 实战表现:

| 试探 | 结果 |
|---|---|
| `xgzh-api/0.1` UA (项目当前) | 503 Akamai |
| 完整 Chrome UA + Accept / Referer 全套头 | 503 Akamai |
| 多次 retry / Backoff | 仍 503 |

非 UA 问题, 是**本地 IP 段被 Akamai 屏蔽** (国内 IP 访问 hkexnews 高频被封, 多家国内项目都报告类似情况)。生产环境若部署在国内, 大概率同样 503; 部署到海外 (Oracle Cloud Free Tier 阿什本) 概率会通, 但仍要测.

**港股免费数据源 spike 结果 (2026-04-26 实测)**:

| 候选源 | URL | 静态可爬 | 数据完整度 | 备注 |
|---|---|---|---|---|
| **hkexnews 申请人页** | `www1.hkexnews.hk/app/listing/applicants/applicants_c.htm` | ❌ Akamai 503 (国内) | ⭐⭐⭐⭐⭐ 官方源 | 海外 IP 可用; 改 Selenium 也行但重 |
| **AAStocks 即将上市** | `aastocks.com/sc/stocks/market/ipo/upcomingipo/company-summary` | 200 OK 但**JS 渲染** | ⭐⭐⭐⭐ 含招股期 / 价格 | HTML 表格内容靠 AJAX 注入, 需 Playwright |
| **etnet 经济通** | `etnet.com.hk/.../ipo_calendar.php` | ❌ 404 路径变了 | - | 路径每年变, 不稳定 |
| **东财港股 IPO** | `data.eastmoney.com/xg/hk/` | ❌ 404 | - | 东财近年弱化港股, 接口废弃 |
| **akshare `stock_ipo_hk_ths`** | (同花顺源) | ✅ 但**返回 A 股**! | - | akshare 名字误导, 实际是 A 股, 不可用 |
| **futu OpenAPI** | `openapi.futunn.com` | ✅ 商业 API | ⭐⭐⭐⭐⭐ | 需注册 / 商业用途合规审核, MVP 不开 |

**推荐方案 (按落地顺序)**:

1. **MVP / 内测期 (Sprint 3)**: 部署到 **海外服务器 (Oracle Cloud 阿什本)** 测 hkexnews 在海外是否可达; 若可达则保留现有 hkex_client.py 即可, 仅工程 IP 受限。
2. **Backup / 国内服务器**: 写 `aastocks_client.py` 适配器, 用 **Playwright async** + **headless Chromium** 渲染 IPO 表格; 预算 ~6h 编码 + 测试; 重型但稳。
3. **长期 / 收入起来后**: 接 **futu OpenAPI** (¥99-299 / 月起), 一次性买全港股 + 美股 + 期权数据; 替换所有自爬。

**临时缓解 (这次不做, 但记下)**: 扩充 cold-start seed 数据集到 ~30 条最近热门 IPO (如 "天星医疗", "速腾聚创") 让用户视觉上觉得数据丰富; 不影响真实数据源接入路径。

**操作建议**:
- 用户当前: 切到 **A 股 tab** 即可看到 200 条真实数据 (akshare CNINFO ✅)
- 港股展示有限: **见 cold-start seed 3 条, 暂时正常, 不是 bug**
- Sprint 3 单独 PR `BE-S3-XXX 港股 IPO 数据源切换` 跟踪上面方案 1+2

### 坑 17：改了 `.env` 后 uvicorn `--reload` 自动重启不生效, OTP whitelist 没短路 ✅ 实战已修

> **现象**: 已经在 `.env` 里加了 `OTP_DEV_FIXED_PHONES=13007458553`, 后端代码也改了 `otp_service.py`, watchfiles 也确认 reload 过 `app/services/otp_service.py`, 但实测调 `/auth/otp/send` 还是走 mock 分支生成随机 OTP, 没有 `[DEV-WHITELIST] code=888888` 日志。
>
> 直接起一个新 Python REPL 调 `get_settings().otp_dev_fixed_phones` 是 `'13007458553'`, **新进程读得到, 老 uvicorn 进程读不到** — 这是关键差异。

**根因**: `uvicorn --reload` 默认 watch 的是 **`*.py` 文件**, 不监听 `.env`。当你只改 `.env`, watchfiles 不感知 → 不 reload。即便顺手改了 `.py` 触发 reload, **uvicorn 的 reload 是 fork 父 reloader 进程**, 父进程在最初启动时已经把环境变量 (含 dotenv 的副作用 `os.environ`) 缓存进内存; reload 后的子 worker 继承父的 env 快照, **新加的 env 变量** 不会出现在子进程里。

实际上 `pydantic-settings` 在 `Settings()` 构造时确实会重读 `.env` 文件本身, 但 **import 阶段父进程已经把 dotenv 加载到 `os.environ`**, 后改的 `.env` 不会回写到内存里的 `os.environ`; 而 `BaseSettings` **优先读 `os.environ` 再读 `.env` 文件**, 所以你看到的就是"老 env, 新代码"的诡异组合。

**解法**: 改 `.env` 后 **整链 kill + 重启 uvicorn**, 不能依赖 watchfiles:

```bash
# 1. 整链 kill (reloader + worker + uv 包装都得清, 否则父进程会把 worker 拉起来)
pkill -f "uv run uvicorn app.main:app"
pkill -f "uvicorn app.main:app"
sleep 2
ps aux | grep uvicorn | grep -v grep   # 应为空

# 2. 重启
cd xgzh/apps/api
nohup uv run uvicorn app.main:app --reload --port 8000 --host 127.0.0.1 \
  > logs/uvicorn.log 2>&1 &

# 3. 验证: 用一个新进程 inspect settings, 与日志对照
uv run python3 -c "from app.core.config import get_settings; s=get_settings(); print(s.otp_dev_fixed_phones, s.vip_user_phone_whitelist)"

# 4. e2e 测一下 (注意旧 rate-limit / OTP 残留要先清, 见下)
redis-cli del "xgzh:rate:otp_send:phone:+8613007458553"
redis-cli del "xgzh:otp:+8613007458553"
curl -s -X POST http://localhost:8000/api/v1/auth/otp/send -H 'Content-Type: application/json' -d '{"phone":"13007458553"}'
# 后端日志应有 `[DEV-WHITELIST] otp.short_circuit phone=+86130****8553 code=888888`
```

**踩坑预防 (3 个细节)**:

1. `Makefile` 加一个 `make restart` target, 把上面的 kill + 重启串起来, 别每次手敲 `pkill` 链。
2. uvicorn 支持 `--reload-include='*.env'` 但 dotenv 还是有 `os.environ` 缓存问题, 不靠谱; **改 `.env` 一律手动重启**, 把这条写进 `RUNBOOK / AGENTS.md` 给所有人。
3. 验证生效一律用"新进程 inspect"双人核对: `uv run python3 -c "..."` 和 `curl + 看日志` 两条都对得上才算修复, 不能只看代码 diff。

---

### 坑 18：UpgradeVipModal 在小程序里点 "稍后再说" / X 没反应 ✅ 实战已修

> **现象**: AI Agent 配额用完后弹 VIP 升级 modal, 点 mask 关弹 OK; 但点底部 "稍后再说" 按钮 + 右上角 X 按钮 **都没反应** (按 view 没有 hover 反馈, modal 也不关). H5 端正常, **只在小程序里出现**。
>
> 控制台没报错, 也没 `console.log` 触发 — 看着像 tap 事件根本没 fire。

**根因**: panel 上写了 `@tap.stop="noop"` 阻止冒泡到 mask, uniapp 编译到 mp-weixin 会变成 `catchtap="noop"`. **catchtap + 空 noop handler** 在 mp-weixin 部分基础库版本下行为有 race: 当 panel 内部还有 `<scroll-view>` 兄弟 + `<view bindtap>` 子节点的时候, mp-weixin 的事件分发偶发把子节点 `bindtap` 当成 panel `catchtap` 的命中目标 → noop 吃掉事件, 子按钮的 onClose / onUpgrade 不 fire。

注: 这不是 100% 复现, 与 wxss flex 嵌套 / scroll-view position 缓存 / 基础库版本都有关系; 但 catchtap noop 这种"只是为了 stop bubble"的写法被官方文档明确不推荐 (见微信社区 [#0000ee8a4...]), 不该出现在生产代码里。

**解法**: 不再用 panel 的 `@tap.stop`, 改成 mask 自己用 `e.target.dataset.role` 判断 — 只在 target 真的是 mask 那一层时才关弹, 点子节点 dataset 不命中, 自然不关:

```diff
- <view v-show="visible" class="uv-mask" @tap="onMaskTap">
-   <view class="uv-panel" @tap.stop="noop">
+ <view v-show="visible" class="uv-mask" data-role="mask" @tap="onMaskTap">
+   <view class="uv-panel" @touchmove.stop.prevent="">
       ...
       <view class="uv-close" hover-class="uv-close-hover" @tap="onClose">×</view>
       <view class="uv-actions">
-        <view class="uv-btn uv-btn-secondary" @tap="onClose">稍后再说</view>
+        <view class="uv-btn uv-btn-secondary"
+              hover-class="uv-btn-secondary-hover" :hover-stay-time="80"
+              @tap="onClose">稍后再说</view>
         ...
       </view>
     </view>
  </view>
```

```ts
// 跨端: H5 e.target 是 DOM, MP e.target 是 {id, dataset, ...}; dataset 同名兼容
function onMaskTap(e: { target?: { dataset?: { role?: string } } }) {
  if (e?.target?.dataset?.role === 'mask') {
    upgrade.close()
  }
}
```

**附带改进**:

1. `hover-class` + `hover-stay-time: 80` 给视觉点击反馈 (mp-weixin 比 `:active` 伪类稳)
2. `@touchmove.stop.prevent=""` 在 panel 上锁定背景滚动, 没它的话 modal 打开时背后还能滑
3. `.uv-close-x` 的 `color` 不再 `var(--color-text-muted)`, 直接写硬色 `#94a3b8` — 小程序 wxss 没有 `:root`, var fallback 失效时 X 会显示成黑色或不可见 (见坑 13 的解释)

**踩坑预防**: 项目内禁止 `@tap.stop="noop"` / `@click.stop=""` 这种空 handler 写法, 理由见上。**真要 stop bubble 必须**:
- 要么把 stop 上移到带逻辑的 handler 里 (例如 onClose 内 ctx.event.stopPropagation()),
- 要么在父节点用 `e.target.dataset / id` 判断, 让 stop 责任归到父节点而非中间层。

---

### 坑 19：测试账号 `13007458553` 每次跑测都被 free 5/天 quota 拦, 要充值才能继续 ✅ 实战已修

> **现象**: 用测试号登录后调 AI Agent, 5 次后弹 VIP 升级 modal, 但 dev 环境根本没有支付通道, 等于卡死。每天滑动窗口刷新前测试用例都跑不完。

**根因**: `app/services/agent/quota.py::_resolve_plan` 只支持 **基于 user_id (UUID) 的 VIP whitelist**:

```python
if str(user.user_id).lower() in settings.vip_user_id_set:
    return QuotaPlan.VIP
```

但 UUID 是注册时才生成的, 测试号根本没法预先 hardcode 到 `.env`; 每次清库重建都要重查 UUID 写一遍, 流程闭环不上。

**解法**: 加一份 **基于手机号** 的 dev VIP whitelist, 与 OTP whitelist 对偶 (同样仅 dev 用):

1. `app/core/config.py` 加字段 + 归一化 set:
   ```python
   vip_user_phone_whitelist: str = ""   # 11 位裸号 / E.164 都行, 逗号分隔

   @property
   def vip_user_phone_set(self) -> frozenset[str]:
       # 归一化: 去 +86 / +852 / +65 前缀, 留裸号; 与 OTP 白名单同语义
       def _bare(p: str) -> str:
           s = p.strip().lstrip("+")
           for prefix in ("86", "852", "65"):
               if s.startswith(prefix) and len(s) > len(prefix):
                   return s[len(prefix):]
           return s
       return frozenset(_bare(s) for s in self.vip_user_phone_whitelist.split(",") if s.strip())
   ```

2. `app/services/agent/quota.py::_resolve_plan` 在 user_id 检查后加一段:
   ```python
   phone_set = settings.vip_user_phone_set
   if phone_set and user.phone:
       bare = user.phone.lstrip("+")
       for prefix in ("86", "852", "65"):
           if bare.startswith(prefix) and len(bare) > len(prefix):
               bare = bare[len(prefix):]; break
       if bare in phone_set:
           return QuotaPlan.VIP
   ```

3. `.env`:
   ```bash
   VIP_USER_ID_WHITELIST=                       # 留空, 或填 UUID
   VIP_USER_PHONE_WHITELIST=13007458553         # 测试号
   ```

4. **重启后端** (见坑 17, `.env` 改动必须硬重启)

**端到端验证 cmd**:

```bash
# 1. 登录拿 token (用 OTP whitelist code 888888)
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/phone \
  -H 'Content-Type: application/json' \
  -d '{"phone":"13007458553","code":"888888"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['tokens']['access_token'])")

# 2. 调 SSE diagnose, 应直出 event:start 不是 429
curl -sN -X POST http://localhost:8000/api/v1/chat/diagnose \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"question":"hi"}' | head -3

# 期望:
# event: start
# data: {"session_id": "...", "model": "..."}
# event: delta
```

后端日志可以再加 `logger.debug(f"resolve_plan user_id={user.user_id} phone={mask_phone(user.phone)} → {plan.value}")` 辅助审计 (生产可关 debug 级)。

**踩坑预防**:

- Sprint 3 接 `vip_memberships` 表后, 这个 phone whitelist 退化为 dev 兜底, **生产 VIP 走表**, 不依赖 `.env` 配置防止漏改炸 (`app_env == prod` 时 phone whitelist 应该被忽略, 与 OTP whitelist 一致 — TODO 待加双重护栏)
- `.env.example` 加注释 `# DEV ONLY, 生产请用 vip_memberships 表`, 避免新人 copy 配置时误带到 prod
- 用户白名单与 phone whitelist 是 **OR 关系**, 任一命中即 VIP; 不要做 AND, AND 没 dev 友好性

---

### 坑 20：me 页 / 用户详情进去就弹 VIP modal, 且 13007458553 登录后还是被卡 quota ✅ 实战已修

**症状**:

1. 打开"我的"个人中心 (me 页), **立刻就弹**升级 VIP 弹窗, 没点过任何"升级"按钮
2. 已登录的白名单用户 (`13007458553`) 进 agent 页点 AI 分析, 还是弹"升级 VIP", 与坑 19 修复矛盾
3. modal 还是关不掉 (即便修了坑 18)

**根因 — 1 个 bug 链, 不是 3 个独立 bug**:

```
[起点] 用户冷启动, 还没登录
        ↓ 点 AI 分析
[anonymous] 后端按 IP 给 2/天, 用完
        ↓ 后端返回 429 quota
[chat store] globalError = { kind: 'quota', ... }
        ↓ 用户在 banner 点 "升级 VIP"
[upgrade modal] visible = true (模块级 ref 单例!)
        ↓ 旧 modal 关闭按钮被 catchtap noop 吃掉 (坑 18, 修了但用户没编译)
[modal 卡死] visible 仍为 true, 关不掉
        ↓ 用户去登录页登录 13007458553
[setSession] 仅更新 accessToken/user, **不动 chat.globalError, 不动 modal.visible**
        ↓ 切到 agent 页 / me 页
[每个挂 <UpgradeVipModal/> 的页面] 模板挂载时读模块级 visible=true → 立即显示
[用户看到] "我刚进 me 页就弹了" / "我登录了 13007458553 还是弹"
```

**关键证据 (后端日志 + redis)**:

```bash
# 13007458553 登录成功了, 12:09 还在用同一 token 操作 favorites:
{"msg": "auth.login.ok user_id=1abe8ea0... phone=+86130****8553"}
{"msg": "favorite.add user_id=1abe8ea0... code=09660.HK ..."}

# 但登录后 chat/diagnose 没有任何 user 维度的 quota key:
$ redis-cli exists "xgzh:rate:agent:user:1abe8ea0-9643-4c3d-954e-3f51b48344a4"
0
$ redis-cli exists "xgzh:rate:agent:vip:1abe8ea0-9643-4c3d-954e-3f51b48344a4"
0
# 只有匿名 IP 维度的:
$ redis-cli --scan --pattern "xgzh:rate:agent:*"
xgzh:rate:agent:anon:127.0.0.1

# 后端最后一个 chat 请求是 12:04 之前的 anonymous 429:
INFO: 127.0.0.1:50366 - "POST /api/v1/chat/diagnose HTTP/1.1" 429
# 12:04 之后无任何新 chat 请求 → 用户报"还是卡"实际是旧 modal 残留, 没真发请求
```

**结论**: VIP whitelist (坑 19 修的后端) **完全生效**, 13007458553 登录后调 chat 一定是 VIP 短路. 但前端 chat store 的 `globalError` + UpgradeModal 单例 `visible` 是"上一次会话"的 stale state, 跨身份切换没清.

**误诊陷阱**:

- 看到 modal 弹就以为是后端 quota 又触发 → 错: 后端日志没新 429
- 看到 me 页弹 modal 就以为 me 页代码有 bug 主动 open → 错: me 页只在用户点 VIP 卡时才 open
- 以为 13007458553 没被识别为 VIP → 错: redis 没新 quota key 说明根本没经过 plan 检查 (modal 卡屏没让用户发请求)

**修法**: 在"语义边界"reset 单例 state — auth setSession / clearSession 时同时清 modal + chat globalError.

#### 1) `composables/upgradeModal.ts` 加 `reset()`

```typescript
function reset() {
  visible.value = false
  source.value = 'manual'
  quota.value = null
}

// 与 close 区别:
// close - 仅 visible=false, 保留 source/quota (退场动画期不闪)
// reset - 全清, 用于"语义边界变化"(登录/登出/store 主动 reset)
```

#### 2) `stores/auth.ts` 在 setSession / clearSession 调 reset

```typescript
function _onSessionChanged() {
  // upgrade 是模块级 ref, 同步 reset
  useUpgradeModal().reset()
  // chat store 走 dynamic import 防 bundle 体积污染 + 防循环
  void import('@/stores/chat').then((mod) => {
    mod.useChatStore().dismissGlobalError()
  })
}

function setSession(resp) {
  // ... 原逻辑 ...
  _onSessionChanged()  // ← 新增
}

function clearSession() {
  // ... 原逻辑 ...
  _onSessionChanged()  // ← 新增
}

// setTokens (silent refresh) 不调: 身份不变, quota 上下文继续有效
```

#### 3) `pages/me/index.vue` onShow 防御性 close

```typescript
function refreshAuthGate() {
  if (!loggedIn.value) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  // me 页本身不应自动弹 modal, gotoVip 是 user-initiated 才 open;
  // 兜底防上一页 stale visible (与 setSession 的 reset 是双保险)
  upgrade.close()
  // ...
}
```

**踩坑预防**:

1. **模块级 ref 单例 ≈ 全局变量**: 跨页面跨 setup 都共享一份, 必须明确"什么时候应该 reset". 一般规则:
   - 用户身份变化 (登录 / 登出) → reset
   - 用户主动新会话 / 切上下文 → reset
   - 同一身份切页面 → 不动 (state 应该跟随用户)

2. **不要相信 "用户会点 X 关 modal"**: 关闭按钮可能因为 (a) 事件冲突 (坑 18) (b) z-index 错位 (c) 安卓返回键不触发 close 等问题失效, **必须有非 user-action 路径来 reset state** (例如登录态变化).

3. **auth store ↔ chat store 单向依赖**: auth → chat 用 dynamic import (`import('@/stores/chat')`), 不要让 chat 反过来 import auth, 否则 bundler 会绕回循环. 看到"main bundle 突然变大"或"chat 模块加载死锁"先怀疑这个.

4. **诊断口诀: 看 redis quota key + 后端日志, 不要看前端 modal**:
   - redis 有该用户的 quota key & 后端有 429 日志 → 真后端拦
   - redis 没 quota key 但 modal 还弹 → 前端 stale state, 90% 是单例 ref / store globalError 没清

5. **setTokens 不要 reset**: silent refresh 是同一身份的 token rotation, 清掉用户当前看的 quota 倒计时 / 错误 banner 反而出 bug. 仅在 setSession (新登录) / clearSession (登出) 调.

6. **跨 store 副作用集中在"高层"store**: auth 是身份层 (上游), chat / favorites / upgrade 是业务层 (下游). 让上游清下游, 不要让下游反向监听上游 — 单向依赖更好维护, IDE 跳转也直观.

---

### 坑 21：流式 LLM 调用没设 timeout, agent 第二轮 LLM hang 死, 前端无限加载 ✅ 实战已修

**症状**:

用户在 AI 诊断里提问 (例如 `"这家公司基本面如何"`), 前端 SSE 起了, 但**再也没动静** — 既没出字, 也没报错, 也不超时. 用户体验是"加载中无限转圈". 5 分钟过去后端日志没任何后续, 前端 console 也没新事件.

**误以为的原因 (错误诊断走了大约 1 小时)**:

1. ❌ 以为是 GLM-4-Flash 不支持 tool calling — 实际支持
2. ❌ 以为是网络问题 — `curl healthz / login / ipos` 都通
3. ❌ 以为是 quota 拦截 — redis 没 quota key (VIP whitelist 已生效)
4. ❌ 以为是前端 stale modal — 看到的"加载中"确实是新请求, 不是 stale
5. ❌ 以为是 chat store globalError 残留 — 后端日志确实有 `POST /chat/diagnose 200 OK` 进来, 是真请求

**真正根因 (后端日志铁证)**:

```
{"msg": "agent.graph.llm_call model=zhipu/glm-4-flash msgs=2 tools=6 temp=0.0", "request_id": "..."}
{"msg": "llm.stream_chat_meta model=zhipu/glm-4-flash provider=zhipu msgs=2 tools=6"}
{"msg": "agent.graph.act step=1 tool_calls=1"}                         ← 第一轮 LLM 决定调 tool
{"msg": "agent.graph.llm_call model=zhipu/glm-4-flash msgs=4 tools=6"} ← 第二轮 LLM (含 tool result)
{"msg": "llm.stream_chat_meta model=zhipu/glm-4-flash msgs=4"}
                                                                       ← 之后 5+ 分钟没任何日志
```

第二轮 LLM 调用 (msgs=4 包含 `role=tool` 的 tool_result message) 时, GLM-4-Flash 偶发性**SSE 通道开了但不发任何 chunk**.

代码层面:

```python
# app/adapters/llm_client.py 旧版 astream_chat_with_meta()
call_kwargs = {
    "model": effective_model,
    "messages": messages,
    "stream": True,
    "stream_options": {"include_usage": True},
    "temperature": use_temp,
    "max_tokens": max_tokens,
    # ❌ 缺 timeout! 同文件非流式版 chat() 是有的, 流式版漏了
}
```

调用链 hang 死的传播路径:

```
GLM-4-Flash 不发 chunk
   ↓
httpx 默认无 read timeout 上限 (LiteLLM 没传 timeout 给它)
   ↓
LiteLLM acompletion 的 async iterator 永远 await chunk
   ↓
agent.graph._call_llm_streaming `async for chunk in stream_iter` 永远不退出
   ↓
SSE handler 永远不 yield end / error event
   ↓
前端 EventSource 收不到任何新事件, 也不知道断了
   ↓
chat store phase='streaming' 永远不切终态
   ↓
用户看见无限"加载中"
```

**修法 (2 行)**:

`app/adapters/llm_client.py` 两处流式入口都加 timeout, 与同文件非流式 `chat()` 保持一致:

```python
# astream_chat_with_meta (BE-S2-007 LangGraph 主循环用)
call_kwargs = {
    "model": effective_model,
    "messages": messages,
    "stream": True,
    "stream_options": {"include_usage": True},
    "temperature": use_temp,
    "max_tokens": max_tokens,
    "timeout": s.llm_request_timeout_seconds,  # ← 默认 60s, 见 .env
}

# stream_chat (Sprint 1 老兼容入口) 同步加
```

LiteLLM 把这个值落到 httpx transport 层, 既覆盖 **connect** 也覆盖 **chunk 间 read timeout**. provider 卡住后 60 秒抛 `httpx.ReadTimeout` → LiteLLM 包成异常 → `astream_chat_with_meta` catch 后 raise `LLMProviderError` → `agent.graph` 已有 except 路径 yield `StepErrorEvent` → SSE 端层把它转成 `event: error` → 前端 chat store 走 `_onAgentError`, banner 显示"模型调用失败"+ 重试按钮.

**端到端验证 (修复后, 同样问题 + 同样 IPO + 同样模型)**:

```
event: start         model=zhipu/glm-4-flash
event: tool_call     get_ipo_basic_info (16ms, 地平线-W 数据)
event: delta         "地" → "平" → "线" → "机器人-W" → "（代码：09660.HK）" ...
event: end           ok=true
```

第二轮 LLM 正常吐字. 排除掉了 GLM-4-Flash 模型本身的兼容性怀疑.

**踩坑预防**:

1. **任何 await 远端 IO 都必须有 timeout 兜底**, 这是绝对铁律. 流式调用尤其容易漏 — "我 yield 了, 出问题流就断了" 这个直觉是错的, SSE / WebSocket / gRPC stream 都可能"通道活着但不发数据" (Half-Open). httpx 默认行为是无限 read timeout 等下一帧.

2. **流式和非流式调用的 kwargs 必须对称**: 同一个 LLM client 的同步 + 异步 + 流 + 非流四个入口, timeout / api_key / temperature 这些"通用"字段必须用同一组 base_kwargs. 这次是因为 `astream_chat_with_meta` 在重写时漏抄了 `chat()` 已有的 timeout, 提示我们应该把 timeout 放进 `_build_completion_kwargs()` 而不是各自手抄. **后续重构 task: 把 timeout 下沉到 `_build_completion_kwargs`** (本次没动, 因为最小修复优先).

3. **Hang 死 != 报错**: 如果异常路径 (LLMConfigError / LLMProviderError) 都有日志而你看到的是"什么日志都没有", 90% 是有 await 永远不返回. 先怀疑 timeout 缺失而不是逻辑 bug.

4. **诊断口诀: 看后端最后一条日志的 logger + ts, 跟"现在时间"差多久**:
   - 差 < 5s: 还在正常处理, 等
   - 差 30-60s: 上游慢, 可能要 timeout 了
   - 差 > 2 min: **几乎必定 hang 死**, 直接看代码里这条日志后面是什么 await
   - 这次就是看到 "msgs=4" 后再无日志超过 5 分钟 → 立即定位到 stream 消费循环

5. **测试要覆盖"第二轮 LLM"**: 单测 + 集成测如果只测"问候"这种一轮回答, 永远碰不到这个 bug. 必须测**会触发 tool_call 的问题**, 让 agent 走完决策 → 调 tool → reflect 这一整个循环. 推荐 fixture: `"这家公司基本面如何" + ipo_code=09660.HK`.

6. **provider 兼容差异**: 同一个 `openai/...` 兼容协议, 不同 provider (硅基 / DeepSeek 官方 / 智谱 paas-v4) 对 SSE 流的实现细节不一样. 智谱 GLM-4-Flash 的 OpenAI 兼容层在 `role=tool` 消息后**偶发**不返回 chunk (实测 ~10% 概率). 这不是模型 bug, 是网关 bug. **不能依赖 provider 行为良好**, 必须客户端兜底超时.

7. **把这条加进 PR review checklist**: "新增 / 修改的 LLM call 有没有 timeout?" 任何 PR 改 `llm_client.py` / `agent/graph.py` 都要明确回答.

---

### 坑 22：多个 uvicorn 实例并存,请求随机命中旧代码进程,误以为修复没生效 ✅ 实战已修

**现象**:

- 后端代码改了 (例如给 LLM stream 加了 `asyncio.wait_for` 兜底), `lsof -i:8000` 看到端口在 listen, curl 直接测 work, 但用户在 H5 / 小程序里测**还是卡**, 后端日志看不到 timeout error 也看不到正常流结束
- 进一步排查: `lsof -nP -iTCP:8000 -sTCP:LISTEN` 显示**两个 PID 同时占着 8000 端口** (这本来不合法, SO_REUSEPORT 才能这样)
- `ps aux | grep uvicorn` 看到至少两条记录, 一个是 nohup 起的, 一个是 `uv run uvicorn ... --reload` 起的

**根因**:

1. **`--reload` 模式天然双进程**: uvicorn `--reload` 会 fork 一个 watchdog 父进程 + 一个真正 listen 的 worker 子进程, `lsof` 看到 2 个 PID 是正常的.
2. **不同 shell 里多次启动后端**: 如果 A 终端先用 `python -m uvicorn ... &` 启了一个, B 终端又用 `uv run uvicorn ... --reload` 起一个, **两个都成功 listen 同一端口** (macOS / Linux 上 `SO_REUSEPORT=1` 时被 kernel 允许), 内核**轮询**把新连接分配到任意一个, 业务上等于"50% 概率命中旧代码".
3. **更阴险的是日志去向不同**: 我自己 `nohup uvicorn ... > 903777.txt 2>&1 &` 起的那个 stdout 在 903777.txt 里, 但 `uv run uvicorn ... --reload` 在另一终端 (e.g. `s007`), stdout 就在那个终端 stdout, **不会进 903777.txt**. 看日志只能看到我那一半进程的请求, 另一半的请求"凭空消失"了, 误以为后端死了 / 卡了.
4. **`uv run` 还可能用独立 venv**: `uv run` 会基于 `pyproject.toml` 解析依赖, 可能装到 `.venv` 之外的目录, 跑的代码版本和 `source .venv/bin/activate` 的不一致 (虽然都 import 同一个 `app/...` 源码, 但站点包版本可能不同).

**修复 / 排查 SOP**:

1. **诊断"是不是有多个进程"**:

   ```bash
   lsof -nP -iTCP:8000 -sTCP:LISTEN     # 应该只看到 1 行 (no-reload) 或 2 行 (reload)
   ps aux | grep -E "uvicorn|app.main" | grep -v grep
   ```

   如果 `ps` 里看到 `--reload` + `nohup` / 没 `--reload` 的两条, 就是双开了.

2. **彻底清场, 强杀所有 uvicorn**:

   ```bash
   pkill -9 -f "uvicorn"
   pkill -9 -f "app.main:app"
   sleep 2
   lsof -nP -iTCP:8000 -sTCP:LISTEN   # 应该输出空
   ```

3. **只起一份, 推荐用 no-reload + stdout 重定向**, 方便 AI 协作时统一看一个日志文件:

   ```bash
   cd xgzh/apps/api && source .venv/bin/activate && \
     uvicorn app.main:app --host 127.0.0.1 --port 8000 \
       > /tmp/xgzh-api.log 2>&1 &
   ```

   开发本地需要 hot-reload 时再加 `--reload`, 但记住**只起一份** — 改完代码 reload 自动重启, 不用手动再 `uvicorn ... &`.

4. **排查"我改的代码到底进 runtime 没"**:

   ```bash
   # a) 确认源码改了
   grep -n "asyncio.wait_for" apps/api/app/adapters/llm_client.py
   # b) 重启后, 看启动日志里是不是从这个文件 import 成功 (没语法错误)
   tail -50 /tmp/xgzh-api.log
   # c) 直接 curl 后端 (绕过前端缓存), 验证后端单点 work
   curl -s -X POST http://127.0.0.1:8000/api/v1/chat/diagnose ...
   ```

**预防**:

- 开发时**只用一种启动方式**: 本地 hot-reload 推荐 `uvicorn ... --reload`, AI 协作 / E2E 测试推荐 nohup + 日志文件. **不要在两个终端各起一个**.
- AI 协作时显式约定: 后端**由 AI 启动**, 用户改完代码不要自己另起. 启动文档里记一下当前后端的 PID, 改 .env 后用 `kill -HUP <pid>` 让 uvicorn reload, 而不是另开新进程.
- `RUNBOOK §Phase 1` 里加一句: "如果你看到 `lsof -i:8000` 输出超过 1 行 (非 reload 模式) 或超过 2 行 (reload 模式), 立刻 `pkill -9 -f uvicorn` 重新启动."

---

### 坑 23：前端 SSE parser 不兼容 CRLF 分隔, fetch 收满数据但 0 个 event 触发, UI 永远卡 loading ✅ 实战已修

**现象**:

- 用户在 H5 浏览器里点 "AI 一键诊断" 后**永远 loading**, 没有 AI 回复也没有错误提示
- F12 Network 面板显示 ``POST /api/v1/chat/diagnose`` **状态 200 OK**, **Transferred 3.39 kB**, **接收 7.39s** — 说明数据**实际从后端流到了浏览器**
- 后端 ``terminals/903777.txt`` 日志显示 ``agent.graph.llm_call`` × 2 + ``agent.graph.act step=1 tool_calls=1`` — 整条 ReAct 链跑完了, 一切正常
- 直接 ``curl -X POST http://127.0.0.1:8000/api/v1/chat/diagnose`` 测后端: 完美流出 ``event: start / tool_call / delta * N``
- 直接 ``curl -X POST http://127.0.0.1:5173/api/v1/chat/diagnose`` 测 vite proxy: 同样完美, 第一个 chunk 230ms 就到, 完全没 buffer
- 但前端的 ``onStart / onDelta / onEnd`` callback **一个都没触发**, store 里 ``phase`` 永远卡在 ``'pending'`` (UI 显示 loading spinner 不消失)

**根因**:

后端 ``sse_starlette.EventSourceResponse`` 默认用 **``\r\n``** (CRLF) 作为 SSE 行尾 + ``\r\n\r\n`` 作为 event 之间的分隔符 — 这是 W3C SSE 规范允许的 (``\n`` / ``\r`` / ``\r\n`` 三种都合法).

但前端 ``utils/sse.ts`` 的 ``parseSSEBuffer`` 写成了:

```typescript
const blocks = buffer.split('\n\n')   // ← 只识别 LF+LF
```

而 ``\r\n\r\n`` 是 ``\r`` + ``\n`` + ``\r`` + ``\n`` 4 个字符, 里面**没有连续 ``\n\n``**. ``split('\n\n')`` 在这种 buffer 上**找不到任何分隔符**, 所有数据全程累积在 buffer 里:

```
[sse-debug] chunk # 1 "event: start\r\ndata: {...}\r\n\r\n"
[sse-debug] chunk # 2 "event: tool_call\r\ndata: {...}\r\n\r\n"
[sse-debug] chunk # 3 "event: delta\r\ndata: {...}\r\n\r\n"
...
[sse-debug] reader done after 9 chunks, buffer remainder len= 3223  ← 全卡 buffer 里!
```

整个 stream 结束时 ``reader done`` 但 ``buffer`` 还有 3223 字节 — 这是所有 SSE event 的原始字节, **0 个被解析出来**, 所以 ``onEvent`` 一次都没调, ``store._onStart / _onDelta / _onEnd`` 全部静默, ``phase`` 永远卡在 ``'pending'``.

curl 之所以**看不出来**这个问题, 是因为终端把 ``\r\n`` 渲染成了普通换行 — 视觉上跟 ``\n\n`` 一样, 但 raw bytes 不一样.

**bug chain**:

1. sse_starlette 写出 ``b"event: start\r\ndata: {...}\r\n\r\n"`` (W3C 严格模式)
2. uvicorn → vite proxy → fetch → ReadableStream 完整透传 (没人改 bytes)
3. 前端 ``decoder.decode(value)`` 拿到 ``"event: start\r\ndata: {...}\r\n\r\n"`` 字符串
4. ``parseSSEBuffer(buffer.split('\n\n'))`` 切不出 block, 全部 ``return remainder = buffer``
5. 下一次 chunk 进来, 还是切不出, 继续累积
6. ``reader done`` 时 buffer = 全部 3kB 数据, 直接丢弃
7. ``opts.onComplete?.()`` 被调, 但**任何 SSE event handler 都没触发过**, store ``_onEnd`` 没被调 → ``phase`` 不切到 ``'done'``
8. UI 永远 spinner

**关键调试技巧**:

直接用 ``JSON.stringify`` 在 console 打印原始 chunk **能立刻看到 ``\r\n``**:

```typescript
const chunk = decoder.decode(value, { stream: true })
console.log('[sse-debug] chunk #', n, JSON.stringify(chunk.slice(0, 200)))
//                                ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
//                                 关键: stringify 把 \r\n 显成字面 "\\r\\n"
```

如果直接 ``console.log(chunk)``, 终端 / DevTools 都把 ``\r\n`` 渲染成换行, 跟 ``\n\n`` 视觉上一样, **看不出来**. ``JSON.stringify`` 把控制字符转义成可见字面量, 一眼看到 ``\r\n``.

**修复**:

``apps/mp/utils/sse.ts`` 的 ``parseSSEBuffer`` 进 split 之前先 normalize 行尾:

```typescript
function parseSSEBuffer(buffer: string, onEvent: (evt: SSEEvent) => void): string {
  // 兼容 \r\n\r\n (sse_starlette / 严格 SSE 规范) 和 \n\n (LF only) 两种分隔.
  // 先把 \r\n 统一成 \n, 再按 \n\n 切 block.
  const normalized = buffer.replace(/\r\n/g, '\n')
  const blocks = normalized.split('\n\n')
  const remainder = blocks.pop() ?? ''
  // ... (per-block parse 逻辑不变)
}
```

为啥不直接 ``split(/\r?\n\r?\n/)``? 因为更复杂的 regex 在长 buffer (几 MB) 上比 ``replace + split`` 慢 3-5 倍, 且 ``replace`` 一次扫描后续 ``startsWith('event:')`` / ``startsWith('data:')`` 也不需要再处理 ``\r``.

**预防**:

1. **SSE parser 有单测覆盖 CRLF / LF / 混合三种 case** — Sprint 3 mp 项目接 vitest 时第一批补:

   ```typescript
   test('parseSSEBuffer handles CRLF', () => {
     const events: SSEEvent[] = []
     parseSSEBuffer('event: a\r\ndata: 1\r\n\r\nevent: b\r\ndata: 2\r\n\r\n', e => events.push(e))
     expect(events).toEqual([
       { event: 'a', data: '1' },
       { event: 'b', data: '2' },
     ])
   })
   test('parseSSEBuffer handles LF', () => { /* 同上, 全 \n */ })
   test('parseSSEBuffer handles split chunks', () => {
     // 模拟 \r\n\r 切在 chunk 边界, 后半段在下一次 chunk 里
   })
   ```

2. **任何"网络层 OK + 前端 0 反应"的诡异 bug, 第一步都该用 ``JSON.stringify`` 打印原始 bytes 看不可见字符** — BOM / CRLF / NBSP / 零宽空格都是这类经典坑. ``console.log(string)`` 永远不够, 必须 stringify.

3. **后端别只信浏览器**: curl 测后端 + curl 测 proxy + curl 测每一跳, 但每一跳的差异只能用 raw bytes 比对 (``curl ... | xxd | head``). 视觉相同的两段输出 raw bytes 可能差很大.

4. **新接 SSE 客户端时直接 fork 业界成熟实现** (例如 ``microsoft/fetch-event-source`` 或 ``@microsoft/fetch-event-source``), 不要自己手写 parser — 他们对 CRLF / 注释行 / id / retry / 多行 data 都处理好了.

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
