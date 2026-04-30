# 02 — 发布流程 (Release Runbook)

> **目的**: 一份可以**跟着敲**的命令级 SOP. 任何一步出错或不符合预期, **停下来**, 走 [`04-rollback-plan.md`](./04-rollback-plan.md) 而不是硬上.
> **预期总耗时**: 从 D1 冒烟到 D5 全量 ≈ **4 ~ 6 小时实操** (审核等待时间不算)
> **环境**: macOS + Node 20 + pnpm 9 + uv + 微信开发者工具 (Stable 1.06+)
> **AppID**: `wxe525868b30a43b96`
> **依赖文档**: `docs/RUNBOOK.md` §部署方案 B Phase 5 + `docs/release/p0-regression-checklist.md`

---

## 阶段总览

```
D1: 冒烟 + 体验版              D2: 提审包 build + 上传        D3: 提交审核
[§1 预上线检查] →             [§2 build + 上传体验版] →     [§3 提交审核]
   30~60 min                       45~90 min                  10 min + 1-3d 等待

                                                                  ↓
D5: 全量放量                  D4: 灰度 5% → 10% → 50%       审核通过通知
[§5 全量 + 发版后任务] ←      [§4 灰度发布]              ←   微信平台
   30~60 min                       2~3h
```

---

## §1. 预上线检查 (D1, 30~60 min)

### 1.1 代码冻结 (5 min)

> 原则: **本次发版后任何对 `apps/api/` / `apps/mp/` 的改动, 必须新开 hotfix 分支**, 不能直接推到 main. main 这一刻代表 v1.0.0 的真相.

```bash
cd /Users/youzi530/lingqiao/demand-engine-team/xgzh
git status                       # 必须干净 (no uncommitted changes 除了本 release 工作包)
git log --oneline -5             # 记下当前 HEAD sha, 写到 §6 部署版本登记
git tag -a v1.0.0 -m "首次正式发版 - 微信小程序"   # 打 tag, 后续回滚有定位
git push origin v1.0.0           # 推到远程, 远程 tag 是不可篡改的"真相"
```

**如果 `git status` 不干净**: 先 commit 当前工作包文档:

```bash
git add docs/deploy/01-2026-04-30-release/ project.config.json
git commit -m "docs(deploy): 首次发版工作包 + 修正 root project.config.json AppID"
```

### 1.2 后端自动化回归 (15~30 min)

```bash
cd apps/api
make ci-integration              # = test-db-init + test-all (1045 + Sprint 6 增量 = ~1123 case)
                                 # 期望: ✅ 0 failed, 0 回归, 0 skip 增加
uv run ruff check                # 期望: All checks passed!
uv run mypy app                  # 期望: Success: no issues found in 134 source files
uv run python -m scripts.check_historical_coverage  # 期望: 退出码 0 (industry / first_day coverage 达 AC)
```

**任一步失败 → STOP**. 走对应 fix 流程, 修完重跑.

### 1.3 前端类型检查 + build smoke (10 min)

```bash
cd ../mp
pnpm install                     # 期望: 锁文件无变更
npx vue-tsc --noEmit             # 期望: 0 errors
pnpm build:mp-weixin             # 期望: dist/build/mp-weixin/ 生成 + 0 warning (除已知 dcloudio nightly 提示)
ls -lh dist/build/mp-weixin/      # 期望: 主包 < 1.5MB, 总包 < 2MB
du -sh dist/build/mp-weixin/      # 期望: 总尺寸 < 4MB (含分包 + 资源)
```

**主包体积超 1.5MB 怎么办**:
1. `pnpm build:mp-weixin -- --analyze` (如果 vite-plugin 支持) 或 微信开发者工具 → 详情 → 代码依赖分析 看大头
2. 大概率是 `MarkdownRenderer` / `wot-design-uni` / 字体, 走 `pages.json` `subPackages` 拆分包
3. 临时 hack: 关 `wot-design-uni` 全局引入, 改成按页 import

### 1.4 后端生产部署核查 (10 min)

> 这一步**只在生产环境就绪后跑**. 如果生产服务器还没起, 退到 §1.5 选 "免费版 cloudflare tunnel" 兜底, 不阻断本次发版 (但用户体验会差).

在生产服务器 (例: 腾讯云轻量上海) 上:

```bash
cd /opt/xgzh/apps/api  # 或你的部署路径
cat .env | grep -E '^(JWT_SECRET|WECHAT_MP_APP_ID|DEEPSEEK_API_KEY|OPS_ADMIN_TOKEN|SENTRY_DSN|ALERT_DINGTALK_WEBHOOK|WECHATPAY_DEV_MODE)='
# 期望全部非空 (除 WECHATPAY_DEV_MODE=true 是本次发版的预期值)
# 任何 = 后面是空的 → STOP, 先填值

uv run alembic current           # 期望: 输出 0015_ipos_price_range (head)
curl -fsSL https://api.<你的域名>/healthz   # 期望: {"status":"ok"}
curl -fsSL -H "X-Admin-Token: $OPS_ADMIN_TOKEN" \
     https://api.<你的域名>/api/v1/admin/dashboard?days=1&format=json | jq .total_users
# 期望: 数字 (≥ 0)
```

**WECHATPAY_DEV_MODE 决策** (2026-04-30 主体定为个人版后更新):
- 本次发版 = `true` (stub) — **个人版不能开微信支付**, 这是长期保持的状态, 不是临时.
- VIP 按钮文案改成 "VIP 即将上线" / "敬请期待", **不要拉起 stub modal** (避免引发用户 "我点了为啥没反应" 的困惑).
  - 改动点: `apps/mp/composables/upgradeModal.ts` 的 `gotoPay()` 改成 `uni.showToast({ title: 'VIP 即将上线, 敬请期待 🎉', icon: 'none' })`.
  - 或: 在 `apps/mp/pages/me/index.vue` / `pages/vip/index.vue` 直接隐藏 VIP 升级 CTA, 改成 "VIP 即将上线" 占位.
- 未来主体升企业 (¥300/yr) 后才能切真支付; 切之前还是要按下面 6 项 + 沙箱真跑.
  - [ ] `WECHATPAY_MCH_ID` (商户号)
  - [ ] `WECHATPAY_APIV3_KEY` (32 位 APIv3 密钥)
  - [ ] `WECHATPAY_CERT_SERIAL_NO` (证书序列号)
  - [ ] `WECHATPAY_PRIVATE_KEY_PATH` (apiclient_key.pem 绝对路径)
  - [ ] `WECHATPAY_NOTIFY_URL` (公网 HTTPS, 例: `https://api.<域名>/api/v1/pay/wechat/notify`)
  - [ ] 沙箱真跑一次 lifecycle (创建订单 → 支付 → 回调 → VIP 状态)

### 1.5 11 主线 H5 + mp-weixin 手测 (30~45 min)

按 [`docs/release/p0-regression-checklist.md`](../../release/p0-regression-checklist.md) §2 主线 × 平台 P0 矩阵走. **必走的红线** (未跑过的不能进入 D2):

#### H5 端 (浏览器, 1024×638 + 380×640)

```bash
cd apps/mp
pnpm dev:h5                      # 起在 http://localhost:5173
```

主线 1-11 全跑, 其中**必跑**:
- 主线 1: 登录页协议勾选在 1024×638 视口可见 (BC-3 验)
- 主线 2: 首页点 "腾讯控股" 中文 IPO 详情页 name 显示正确 (BC-4 验)
- 主线 4: AI 诊断输入 "xxx 必涨" → 后端真挡掉 (合规线必须验, 否则提审会被打回)
- 主线 8: 切深 / 浅色, `uni-page-body` 背景跟随, 不留浅色残留 (BC-8 验)
- 主线 11: 社区发 "加我微信 vx 12345" → 立即 reject + 自见 (合规线)

#### mp-weixin 端 (微信开发者工具)

```bash
pnpm dev:mp-weixin               # CLI 编译到 dist/dev/mp-weixin/
# 微信开发者工具 → 打开项目 → 项目根目录填: /Users/youzi530/lingqiao/demand-engine-team/xgzh/apps/mp/dist/dev/mp-weixin/
# AppID 自动从 wxe525868b30a43b96 读, 无需手填
```

主线 1-11 同样跑, **mp-weixin 特有**:
- 主线 1: 微信一键登录 (`POST /auth/login/wechat-mp` → `code2Session` → user 入库)
- 主线 6: VIP 升级 → 拉起微信支付 stub modal (本次预期不真扣款, 验证流程通)
- 主线 9-11: 中签录入 / 知识详情 / 社区发帖在真机 5 寸屏 (380×640) 滚动 + 长按 + 表格横滚正常

#### 真机扫码 (5 步 P0 冒烟)

> 微信开发者工具 → 预览 → 真机扫码 (体验码, 当前用户必须是开发者 / 体验者白名单)

5 个白名单内测用户每人跑一遍:
1. 注册 (手机号 + OTP)
2. 浏览首页 IPO 列表 + 中文 name 正确
3. 点详情页 → AI 诊断 → 收到流式回答 + 引用源底部抽屉能打开
4. 点 VIP 升级 → 弹 modal (stub 不真扣款)
5. 关掉 → 重启微信 → 重进 → 之前的中签记录 / 自选还在

**5 个用户全跑过 + 0 P0 + 0 P1 → 进入 D2**.

---

## §2. build + 上传体验版 (D2, 45~90 min)

### 2.1 修复 D1 内测反馈 (变长, 看反馈量)

每条反馈一个 commit, 写明 BUG-ID + 修复要点 (参考 `docs/bug/2026.04.29-bug.md` 的格式):

```bash
git checkout -b hotfix/D1-feedback-001
# 修代码...
git commit -m "fix(BUG-D1-001): xxx"
git push origin hotfix/D1-feedback-001
# 自我 review 后 merge 到 main
```

修完所有 P0/P1 → 重跑 §1.2 + §1.3 全套 → 全绿才出包.

### 2.2 出提审 build (5 min)

```bash
cd apps/mp
rm -rf dist/build/                # 干净 build, 防 stale 文件
pnpm build:mp-weixin              # NODE_ENV=production
ls -lh dist/build/mp-weixin/app.js  # 期望: 单文件 < 800KB
du -sh dist/build/mp-weixin/      # 期望: < 4MB
cat dist/build/mp-weixin/project.config.json | grep appid
                                  # 期望: "appid": "wxe525868b30a43b96"
```

### 2.3 微信开发者工具上传 (10 min)

> 不要用 CLI miniprogram-ci 第一次, 第一次必须用 GUI 操作能看到每个对话框. CI/CD 二次发版时再切 ci.

1. 打开微信开发者工具 (Stable 1.06+)
2. **打开项目** → 项目根目录填:
   ```
   /Users/youzi530/lingqiao/demand-engine-team/xgzh/apps/mp/dist/build/mp-weixin/
   ```
   **不要填** `apps/mp/` (源码根, 没 app.json 会报错)
3. 工具识别 AppID = `wxe525868b30a43b96` (自动)
4. 顶部菜单 **详情** → **本地设置** → **上传代码时校验合法域名以及 TLS 版本** ✅ (生产必须开)
5. 顶部菜单 **详情** → **项目设置** → 域名信息核对:
   - request 合法域名: `https://api.<你的域名>` ✅ 已加
   - 不合法 → 去 [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发管理 → 开发设置 → 服务器域名 加白
6. 点击右上角 **上传** 按钮
7. 弹窗填写:
   - 版本号: `1.0.0`
   - 项目备注: `首次正式发版 - 港A股打新 + AI 分析 + CRS + 中签记账 + 知识库 + 社区 v1.0.0`
8. 点击 **上传**, 等待 ~30s 推上微信平台

### 2.4 设置体验版 + 内测最后一次 (15 min)

1. [mp.weixin.qq.com](https://mp.weixin.qq.com) → 版本管理 → 看到刚上传的 1.0.0 在 "开发版本" 列
2. 点 **选为体验版**
3. 体验版二维码出现, 用微信扫码进入体验版
4. 重跑 §1.5 5 步 P0 冒烟 1 遍 (手机扫码, 不是开发者工具)
5. 重点验:
   - **请求真的打到生产 backend**: `https://api.<你的域名>` 而不是 localhost
   - **微信一键登录在真机能跑**: 因为 dev 模式开 "不校验合法域名" 无所谓, **体验版起这个开关失效, 必须真域名**
   - **HTTPS 证书有效**: 浏览器 / 微信都不报警告
6. 5 步 P0 全过 → 进入 D3 提审

**任何一步失败 → STOP**. 大概率是:
- 域名没加白 → 加白, 重新 build, 重新上传
- 后端没起 / 没备案 → 解决后再上传
- AppSecret 没配 → 后端 `.env` `WECHAT_MP_APP_SECRET` 没填

---

## §3. 提交审核 (D3, 10 min + 1~3 工作日等待)

### 3.1 提交 (10 min)

1. [mp.weixin.qq.com](https://mp.weixin.qq.com) → 版本管理 → 1.0.0 体验版 那一行 → 点 **提交审核**
2. 弹窗填写 (内容直接从 [`03-release-notes.md`](./03-release-notes.md) §"提审填写卡" 复制):

| 字段 | 内容 |
|------|------|
| **小程序类目** | 金融业 → 财经资讯 (推荐) |
| **功能页面** | (微信会自动扫描页面, 你确认前 3-5 个核心页面: pages/index/index, pages/ipo/detail, pages/me/index) |
| **测试账号** | 手机号: `13800138000` 验证码: `666666` (mock 通道) |
| **测试备注** | "测试账号通过 mock OTP 通道登录, 真实环境通过阿里云 SMS 短信. 完整功能体验需注册后授权微信一键登录." |
| **业务说明** | 复制 [`03-release-notes.md`](./03-release-notes.md) §业务说明 整段 |
| **隐私协议** | `https://<你的域名>/privacy.html` |
| **用户协议** | `https://<你的域名>/terms.html` |

3. 点 **提交**, 状态变为 "审核中"
4. 邮箱 (youzi530@outlook.com) 会收到一封微信审核确认邮件, 保留备查

### 3.2 等待期 (1~3 工作日)

期间**不要再上传新版本**, 否则当前审核可能被无效化.

可做的事:
- 准备 [`03-release-notes.md`](./03-release-notes.md) 的用户公告文案 (公众号 + 朋友圈)
- 配 Sentry alerts (Sprint 6 起的 `SENTRY_DSN` 已接, 配 ENV: `prod` 的 issue 邮件提醒)
- 配 DingTalk 告警 (`docs/runbooks/error_rate_high.md` 测试链路那段跑一次, 验证钉钉群能收到)
- 准备客服微信群 / 二维码

### 3.3 审核结果

**通过** → 邮箱收到 "审核通过" 通知 → 进入 §4 灰度
**驳回** → 邮箱收到驳回原因 → 修复后重新提审, **驳回常见原因**:
1. 服务器域名没加白 → 走 §2.3 步骤 5 加白
2. 类目不符 → 改成 "财经资讯"
3. 红线词漏放 → 全文 grep AI 输出, 检查后端 `tests/test_forbidden_pattern_filter.py` 是否有遗漏 pattern
4. 隐私协议缺失 / 404 → H5 部署单页
5. 资质不全 → 个人 → 企业认证 (¥300/yr)

驳回 → 修复 → 重新走 §2 + §3, 不要省步骤.

---

## §4. 灰度发布 (D4, 2~3h, 含观察期)

### 4.1 5% 灰度 (30 min 观察)

1. [mp.weixin.qq.com](https://mp.weixin.qq.com) → 版本管理 → 审核通过的 1.0.0 那一行 → **发布**
2. 弹窗选 **灰度发布** (默认是 "全量发布", 必须改)
3. 比例选 **5%**
4. 点 **确定**, 发布生效
5. **立即开监控** 3 个面板:
   - DingTalk 告警群: 等 30 min, 期间不应有 P0 / P1 告警
   - Sentry: `https://sentry.io/.../issues/?environment=prod` 按 last_seen 排序, 看新 issue
   - 后端 dashboard: `curl -H "X-Admin-Token: $OPS_ADMIN_TOKEN" https://api.<你的域名>/api/v1/admin/dashboard?days=1&format=html | open -f -a Safari` (或浏览器直接看)
6. **30 min 观察通过条件** (全部满足才放下一档):
   - [ ] API 5xx 占比 < 1%
   - [ ] 0 红线词触发 (Sentry breadcrumb)
   - [ ] 0 死链 (前端 404 报错)
   - [ ] DingTalk 无 P0/P1 告警
   - [ ] Sentry 无新增 critical issue
   - [ ] 微信小程序后台 → 用户反馈, 0 投诉
7. 通过 → 进入 §4.2 10%
8. 不通过 → 翻 [`04-rollback-plan.md`](./04-rollback-plan.md), 决定是 "停止灰度等修" 还是 "回退到旧版本" (本次首发**没有旧版本可回退**, 实际是 "停止灰度" + 修复重新提审)

### 4.2 10% / 50% / 100% (每档 30~60 min)

重复 §4.1 流程, 只改比例:
- 10% 档: 30 min 观察
- 50% 档: 60 min 观察 (流量明显放大, 需要更长窗口看 DB / Redis 是否撑得住)
- 100% 档: 全量, **完成发版**

每档观察通过条件同 §4.1 步骤 6.

---

## §5. 全量后续 (D5, 30 min~半天)

### 5.1 用户公告

1. **小程序自身**: 不需要 (微信会自动通知体验过的用户更新)
2. **公众号**: 复制 [`03-release-notes.md`](./03-release-notes.md) §用户公告文案, 推一篇
3. **朋友圈 / 内测群**: 体验版二维码可以撤了 (体验版本会自动失效), 改贴正式版小程序码 + 一句话推广

### 5.2 监控持续

发版后 **24h 内** 每 4h 看一次:
- DingTalk 告警群是否有 P0 / P1
- Sentry issue trend
- 后端 dashboard 关键指标 (DAU / 5xx / quota / 红线词)

发版后 **7 天内** 每天看一次, 写到 `99-postmortem.md`:
- 实际 DAU 曲线
- 实际错误率
- 用户反馈 top 3
- 跟规划差异
- 下次发版的改进点

### 5.3 关闭体验版 / 测试账号

- 体验版二维码自动失效 (不用手动撤)
- 微信小程序后台 → 成员管理 → 体验者列表, 把内测白名单用户保留 (下次发版还要用)
- 测试账号 (`13800138000`) 在 BE `users` 表里加备注 `[TEST]`, 防止真实用户冲突

### 5.4 v1.0.0 归档

```bash
git checkout main
git pull
git log --oneline | head -1      # 应该是 D2 的最后一个 hotfix commit (或 docs(deploy))
# 确认 v1.0.0 tag 在正确 commit:
git show v1.0.0 | head -5
```

如果 D2 修复后 v1.0.0 tag 漏更新 → 强制更新 (谨慎):

```bash
git tag -fa v1.0.0 -m "首次正式发版 - 微信小程序 (含 D1 内测反馈修复)"
git push origin v1.0.0 --force-with-lease
```

---

## §6. 部署版本登记表

> 每次发版完成后填写, 用于回滚定位 + 审计.

| 字段 | 值 |
|------|-----|
| 版本号 | v1.0.0 |
| versionCode | 100 |
| 发版日期 | 2026-04-30 ~ 2026-05-06 |
| Git tag | v1.0.0 |
| Git commit sha | (D2 最后一个 hotfix 后填) |
| 后端 alembic head | 0015_ipos_price_range |
| 后端镜像 tag | (生产部署后填, 例: `xgzh-api:v1.0.0`) |
| 微信审核通过日期 | (D4 实际填) |
| 全量发布日期 | (D5 实际填) |
| 发版人 | youzi530 |

---

## §7. 常用故障速查

| 现象 | 可能原因 | 速查动作 |
|------|---------|---------|
| 上传时报 "AppID 与项目设置不符" | 工具默认 AppID 错 | 微信开发者工具 → 详情 → AppID 改 `wxe525868b30a43b96` |
| 上传时报 "未配置服务器域名" | 没在小程序后台加白 | mp.weixin.qq.com → 开发管理 → 开发设置 → 服务器域名 加 |
| 上传时报 "包体积超 2MB" | 主包资源太多 | `pages.json` `subPackages` 拆分包, 或砍 `wot-design-uni` 全局引入 |
| 体验版扫码白屏 | 后端 `/healthz` 502 | curl 验后端, 重启 uvicorn |
| 体验版 AI 流式无响应 | LLM key 未配 / 限流 | `apps/api/.env` `DEEPSEEK_API_KEY` 检查; LLM provider 后台看用量 |
| 灰度后 DingTalk 持续告警 5xx | 见 [`docs/runbooks/error_rate_high.md`](../../runbooks/error_rate_high.md) | 按 P0/P1/P2 分级处理 |
| 微信审核驳回 "AI 输出违规" | 红线词漏放 | 检查 `tests/test_forbidden_pattern_filter.py` + 看驳回截图复现 |

更详细见 [`04-rollback-plan.md`](./04-rollback-plan.md).

---

## §8. 二次发版的 CI 化路径 (展望, 本次不做)

> 第二次发版起, 推荐切 `miniprogram-ci`:
> ```bash
> # apps/mp/scripts/upload-mp.ts
> import ci from 'miniprogram-ci'
> const project = new ci.Project({
>   appid: 'wxe525868b30a43b96',
>   type: 'miniProgram',
>   projectPath: 'dist/build/mp-weixin',
>   privateKeyPath: '~/.config/xgzh/private.wxe525868b30a43b96.key',  // 微信小程序后台 → 开发管理 → 开发设置 → 小程序代码上传 申请密钥
>   ignores: ['node_modules/**/*'],
> })
> await ci.upload({
>   project,
>   version: '1.0.1',
>   desc: 'CI 自动上传',
>   robot: 1,
> })
> ```
> 接 GitHub Actions / 阿里云效, 每次 push tag `v*` 自动出包 + 上传体验版 + DingTalk 通知. 本次先手动, 跑过 1 次完整发版后再迭代到 CI.

---

> 🎯 **执行原则**: 不确定就停, 不抢节奏, 不省步骤. 提审通过 ≠ 上线安全, 灰度才是真考验.
