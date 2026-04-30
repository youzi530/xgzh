# 05 — 上线前 Checklist (Pre-Release Checklist)

> **打印 / 勾选用**. 任何一项 ❌ 都不要点 "上传" / "提交审核" / "发布".
> **每条都对应可执行命令或链接**. 不要凭感觉勾.
> **建议打印一份**, 边操作边划.

---

## 操作员: ____________  操作日期: ____________  当前阶段: __________

---

## 阶段 D1 · 体验版前 (代码冻结 → 内测)

### A. 后端自动化绿 (15~30 min)

```bash
cd apps/api
make ci-integration            # ~1123 case
uv run ruff check
uv run mypy app
uv run python -m scripts.check_historical_coverage
```

- [ ] **A1** `make ci-integration` 全绿, 0 failed, 0 回归
- [ ] **A2** `uv run ruff check` → All checks passed!
- [ ] **A3** `uv run mypy app` → Success
- [ ] **A4** `check_historical_coverage` 退出码 0
- [ ] **A5** alembic head = `0014_community` (`uv run alembic current`)

### B. 前端类型 + build smoke (10 min)

```bash
cd apps/mp
pnpm install
npx vue-tsc --noEmit
pnpm build:mp-weixin
```

- [ ] **B1** `pnpm install` 锁文件无变更
- [ ] **B2** `npx vue-tsc --noEmit` → 0 errors
- [ ] **B3** `pnpm build:mp-weixin` 成功 + 无 error
- [ ] **B4** `dist/build/mp-weixin/` 主包 < 1.5MB (`ls -lh dist/build/mp-weixin/app.js`)
- [ ] **B5** `dist/build/mp-weixin/` 总包 < 4MB (`du -sh dist/build/mp-weixin/`)
- [ ] **B6** `dist/build/mp-weixin/project.config.json` AppID = `wxe525868b30a43b96`

### C. 11 主线手测 (H5 + mp-weixin, 30~45 min)

> 详见 [`docs/release/p0-regression-checklist.md`](../../release/p0-regression-checklist.md) §2

- [ ] **C1** 主线 1 (注册/登录): H5 + mp-weixin 都跑过, 含 BC-3 协议勾选
- [ ] **C2** 主线 2 (浏览首页): 中文 IPO 名 BC-4 验过
- [ ] **C3** 主线 3 (文章 + TL;DR): H5 + mp-weixin 跑过
- [ ] **C4** 主线 4 (AI 诊断): SSE 流式 + 引用源抽屉 + **红线词触发后端真挡掉**
- [ ] **C5** 主线 5 (券商对比): 真跳转一次, conversion_events 有记录
- [ ] **C6** 主线 6 (VIP 升级): 拉起微信支付 stub modal (本次预期不真扣款)
- [ ] **C7** 主线 7 (反馈表): 触发限流 toast 见过
- [ ] **C8** 主线 8 (主题 + UTM): BC-8 主题切换 / `uni-page-body` 不漏白底
- [ ] **C9** 主线 9 (中签记账): 创建账户 + 录入中签 + 主页汇总
- [ ] **C10** 主线 10 (知识库): 3 篇 sample 全渲染 + TOC 抽屉
- [ ] **C11** 主线 11 (社区 UGC): 试发 "加我微信 vx 12345" → 立即 reject

### D. 真机扫码 5 步 P0 (5 个白名单内测用户, 各 5 步)

- [ ] **D1** 用户 1 跑通 5 步 (注册 → 列表 → 详情 → AI → VIP)
- [ ] **D2** 用户 2 跑通
- [ ] **D3** 用户 3 跑通
- [ ] **D4** 用户 4 跑通
- [ ] **D5** 用户 5 跑通
- [ ] **D6** 5 个用户**全部** 0 P0 + 0 P1 (才能进 D2 提审包阶段)

### E. 代码冻结 + tag

```bash
cd /Users/youzi530/lingqiao/demand-engine-team/xgzh
git status                     # 干净
git tag -a v1.0.0 -m "首次正式发版 - 微信小程序"
git push origin v1.0.0
git log --oneline -1           # 记下 sha 写到 02-release-runbook.md §6
```

- [ ] **E1** `git status` 干净 (本工作包 commit 后)
- [ ] **E2** v1.0.0 tag 已推到远程
- [ ] **E3** v1.0.0 tag commit sha 写到 `02-release-runbook.md` §6 部署版本登记

---

## 阶段 D2 · 提审包前 (体验版 → 提审)

### F. 生产后端就绪 (10 min)

> 在生产服务器上跑.

```bash
cd /opt/xgzh/apps/api
cat .env | grep -E '^(JWT_SECRET|WECHAT_MP_APP_ID|DEEPSEEK_API_KEY|OPS_ADMIN_TOKEN|SENTRY_DSN|ALERT_DINGTALK_WEBHOOK)='
uv run alembic current
curl -fsSL https://api.<你的域名>/healthz
```

- [ ] **F1** `JWT_SECRET` 已填 32+ 字节随机串 (不是 dev 默认值)
- [ ] **F2** `WECHAT_MP_APP_ID` + `WECHAT_MP_APP_SECRET` 填好 (mp 微信公众平台拿)
- [ ] **F3** `DEEPSEEK_API_KEY` 或 `SILICONFLOW_API_KEY` 填好 + 余额 > ¥50
- [ ] **F4** `OPS_ADMIN_TOKEN` 填 32+ 字节随机串
- [ ] **F5** `SENTRY_DSN` 填好 (生产环境必须)
- [ ] **F6** `ALERT_DINGTALK_WEBHOOK` + `ALERT_DINGTALK_SECRET` 填好
- [ ] **F7** `WECHATPAY_DEV_MODE=true` (本次发版预期保持 stub, 不切真支付)
- [ ] **F8** `alembic current` = `0014_community`
- [ ] **F9** `curl https://api.<你的域名>/healthz` → 200 OK
- [ ] **F10** Admin dashboard 能打开: `curl -H "X-Admin-Token: $OPS_ADMIN_TOKEN" https://api.<你的域名>/api/v1/admin/dashboard?days=1&format=json | jq .`

### G. 微信小程序后台配置 (10 min)

> [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发管理 → 开发设置

- [ ] **G1** AppID = `wxe525868b30a43b96`
- [ ] **G2** AppSecret 已生成 + 存到生产 `.env` (不要在本文件留)
- [ ] **G3** 服务器域名 → request 合法域名加了 `https://api.<你的域名>`
- [ ] **G4** 服务器域名 → uploadFile / downloadFile 加了 (如有图片上传, 没有可跳)
- [ ] **G5** **业务域名** 留空 (本次无 web-view 需求)
- [ ] **G6** 体验者列表加了 5 个内测白名单微信号
- [ ] **G7** 类目设置 = **"工具 → 信息查询"** (个人版必选; 个人版严格禁止金融业 / 证券类目)
- [ ] **G8** ICP 备案号已填 + 备案主体与小程序主体一致 (个人备案对应个人小程序)

### H. 资质 / 合规 (一次性, 之前没做要先补)

- [x] **H1** 小程序主体 = **个人版** ¥0/yr (2026-04-30 拍板)
- [x] **H2** ICP 备案完成 (用户已声明 ✅)
- [ ] **H3** `https://<你的域名>/privacy.html` 200 OK 真实可访问
- [ ] **H4** `https://<你的域名>/terms.html` 200 OK
- [ ] **H5** `https://<你的域名>/risk.html` 投资风险提示 200 OK
- [ ] **H6** `https://<你的域名>/community-rules.html` 200 OK (社区 UGC 用)
- [ ] **H7** 法务签字 (UGC 协议 + 社区规则) — `docs/release/p0-regression-checklist.md` §4 红线项
- [ ] **H8** **个人版降敏化清单** (新增):
  - [ ] H8a VIP 按钮文案改成 "VIP 即将上线 / 敬请期待", **不拉起任何支付 modal** (`apps/mp/composables/upgradeModal.ts` `gotoPay()` 改 toast)
  - [ ] H8b 社区考虑降级到 "只读 + 编辑精选" 模式 (个人版 UGC 监管严格, 二选一: 保留 UGC 但严控 / 临时关闭让个人版先过审)
  - [ ] H8c 全应用 AI 输出 + 文章 / 详情页 / 业务说明 grep 一遍 "投资建议 / 推荐 / 必涨 / 稳赚 / 收益 / 涨" 等触发词, 个人版审核员对这些更敏感

### I. 提审包 build (5 min)

```bash
cd apps/mp
rm -rf dist/build/
pnpm build:mp-weixin
```

- [ ] **I1** `dist/build/mp-weixin/` 干净生成 (rm 后重 build)
- [ ] **I2** 主包 < 1.5MB / 总包 < 4MB
- [ ] **I3** AppID 在产物里正确

### J. 微信开发者工具上传 (10 min)

> 工具 → 打开项目 → 项目根目录: `/<repo>/xgzh/apps/mp/dist/build/mp-weixin/`

- [ ] **J1** 工具识别 AppID = `wxe525868b30a43b96`
- [ ] **J2** **本地设置** → "上传代码时校验合法域名以及 TLS 版本" ✅ 开 (生产必须开)
- [ ] **J3** 上传按钮 → 版本号填 `1.0.0`
- [ ] **J4** 项目备注填 "首次正式发版 - 港A股打新 + AI 分析 + CRS + 中签记账 + 知识库 + 社区 v1.0.0"
- [ ] **J5** 上传成功 (~30s 推送), 在 mp.weixin.qq.com → 版本管理 → "开发版本" 列看到 1.0.0

### K. 设置体验版 + 真机扫码 (15 min)

- [ ] **K1** mp.weixin.qq.com → 版本管理 → 1.0.0 → "选为体验版"
- [ ] **K2** 体验版二维码用真手机微信扫码进入
- [ ] **K3** 请求真的打到生产 `https://api.<你的域名>` (浏览器 / 抓包验证)
- [ ] **K4** 微信一键登录在真机能跑通 (本地 dev "不校验合法域名" 开关在体验版无效)
- [ ] **K5** 5 步 P0 在真机跑过 1 遍

---

## 阶段 D3 · 提交审核

### L. 提审表单 (10 min)

> 复制 [`03-release-notes.md`](./03-release-notes.md) §A 提审填写卡每段.

- [ ] **L1** 类目 = "财经资讯" (或备选)
- [ ] **L2** 业务说明整段复制
- [ ] **L3** 测试账号 = `13800138000` / `666666` (mock OTP)
- [ ] **L4** 测试备注复制
- [ ] **L5** 隐私协议 / 用户协议 URL 真实可访问 (再次验证)
- [ ] **L6** 提交后状态 = "审核中"
- [ ] **L7** 邮箱 (youzi530@outlook.com) 收到审核确认邮件

### M. 等待期 (1~3 工作日, 不要新动作)

- [ ] **M1** 不再上传新版本 (会无效化当前审核)
- [ ] **M2** DingTalk 告警链路测试 1 次 (`docs/runbooks/error_rate_high.md` §测试链路 跑一遍)
- [ ] **M3** Sentry alerts 配 `environment: prod` issue 邮件提醒
- [ ] **M4** 客服 / 反馈接收路径就位 (微信号 + me 页反馈 endpoint 验证)

---

## 阶段 D4 · 灰度发布

### N. 5% 灰度

> mp.weixin.qq.com → 版本管理 → 1.0.0 → 发布 → 灰度发布 → 5%

- [ ] **N1** 选 "灰度发布" 不是 "全量"
- [ ] **N2** 比例填 5%
- [ ] **N3** 立即开 3 个监控面板:
  - DingTalk 告警群
  - Sentry `environment=prod` issues
  - `https://api.<你的域名>/api/v1/admin/dashboard?days=1&format=html`
- [ ] **N4** **30 分钟观察期** 全部满足:
  - [ ] N4a API 5xx 占比 < 1%
  - [ ] N4b 0 红线词触发 (Sentry breadcrumb 检查)
  - [ ] N4c 0 死链 (FE 404 报错)
  - [ ] N4d DingTalk 无 P0/P1 告警
  - [ ] N4e Sentry 无新增 critical issue
  - [ ] N4f 微信小程序后台 → 用户反馈, 0 投诉

### O. 10% / 50% / 100% 各档

每档重复 N1-N4, 比例改对.

- [ ] **O1** 10% 档观察 30 min 全过
- [ ] **O2** 50% 档观察 60 min 全过 (流量明显放大)
- [ ] **O3** 100% 档全量发布完成

---

## 阶段 D5 · 全量后

### P. 用户公告

- [ ] **P1** 公众号推 [`03-release-notes.md`](./03-release-notes.md) §B 用户公告文案
- [ ] **P2** 内测群通告 + 撤体验版二维码 (体验版自动失效, 通告改贴正式版小程序码)
- [ ] **P3** 朋友圈短文推送 (可选)

### Q. 后续监控

- [ ] **Q1** 24h 内每 4h 看一次告警 + Sentry + dashboard
- [ ] **Q2** 7 天内每天看一次, 跟规划差异写到 `99-postmortem.md`
- [ ] **Q3** 关闭测试账号 / 加 `[TEST]` 备注防真实用户冲突

### R. 归档

- [ ] **R1** v1.0.0 tag 在正确 commit (D2 hotfix 后再次确认)
- [ ] **R2** `docs/deploy/01-2026-04-30-release/02-release-runbook.md` §6 部署版本登记表填完
- [ ] **R3** 写 `99-postmortem.md` (即使一切顺利也写, 下次发版能复用经验)

---

## 紧急中止 (任何阶段)

> 任何 ❌ 都不要点下一步.
>
> 提审已提交但发现致命问题 → mp.weixin.qq.com → 版本管理 → 撤回审核 (能撤就撤, 不能撤等驳回)
>
> 灰度已发但出 P0 → 立即翻 [`04-rollback-plan.md`](./04-rollback-plan.md), **5 分钟决策树**
>
> 全量已发但出 P0 → 同上, 但首次发版**没有旧版本可回退**, 主路径是停止灰度 + BE 回退 / feature flag 关停 / 紧急 hotfix 重新提审

---

> 🎯 **使用提示**: 这份 checklist 是给你 "在每个阶段开始前快速确认" 用的, 不是替代 [`02-release-runbook.md`](./02-release-runbook.md). 命令级细节看 runbook, 决策点看本 checklist.
