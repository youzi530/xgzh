# 01 — 2026-04-30 首次发版工作包

> **小程序**: 新股智汇 (XGZH) · AppID `wxe525868b30a43b96`
> **版本**: v1.0.0 (versionCode `100`) — **首次正式发版**
> **决策人**: youzi530@outlook.com (小程序管理员邮箱 / 同 mp 后台登录账号)
> **生成时间**: 2026-04-30 (按 vibe coding 模式一次性铺好 5 份发布材料)

---

## 本目录是什么

这是 **首次正式发版的工作包**, 5 份文档配 1 份原始输入, 按发版动作的时间线串起来:

| # | 文件 | 给谁看 | 核心问题 |
|---|------|--------|----------|
| 0 | [`00-input.md`](./00-input.md) | 自己留底 | 用户最初提的需求清单, 用来对照交付 |
| 1 | [`01-release-plan.md`](./01-release-plan.md) | 老板 / 团队对齐 | **发布什么? 什么时候? 怎么放量?** (灰度策略 + 时间表) |
| 2 | [`02-release-runbook.md`](./02-release-runbook.md) | 操作的人 (你自己) | **一步一步怎么做?** (从 build 到提审到发布的命令级 SOP) |
| 3 | [`03-release-notes.md`](./03-release-notes.md) | 提审审核员 / 用户 | **这一版到底有什么?** (功能清单 + 合规说明 + 用户能感知的能力) |
| 4 | [`04-rollback-plan.md`](./04-rollback-plan.md) | 出事时的你 | **崩了怎么办?** (按严重级 P0/P1/P2 分别的止损动作) |
| 5 | [`05-pre-release-checklist.md`](./05-pre-release-checklist.md) | 自己 / QA 同伴 | **能不能放行?** (可勾选, 走完才敢上传体验版) |

---

## 推荐阅读顺序

**第一次看**: 0 → 1 → 5 → 2 → 3 → 4

**正式发版时**:
1. 先把 [`05-pre-release-checklist.md`](./05-pre-release-checklist.md) 全勾过 (任何一项 ❌ 都不要点 "上传")
2. 按 [`02-release-runbook.md`](./02-release-runbook.md) 逐步执行 (build → 体验版 → 提审 → 发布)
3. 把 [`03-release-notes.md`](./03-release-notes.md) 的 "提审填写卡" 一段贴到微信小程序提审表单
4. 万一出事翻 [`04-rollback-plan.md`](./04-rollback-plan.md), 不要现凑

---

## TL;DR · 一段话发版概要

> 2026-04-30 起, 用 **5 天 (4-30 ~ 5-04)** 完成首次正式发版:
>
> - **Day 1 (今天)**: 跑完 `make ci-integration` + `vue-tsc` + 11 主线 H5/mp-weixin 手测, 出体验版二维码, **5 个白名单内测用户** 真机覆盖 5 步 P0 (登录 → 列表 → 详情 → AI → VIP)
> - **Day 2**: 内测反馈 P0 全修 + `pnpm build:mp-weixin` 出提审包, 微信开发者工具 **上传 1.0.0**, 配体验版二维码再扫一次
> - **Day 3**: **提交审核** (港股金融类目, 1-3 工作日)
> - **Day 4-5**: 审核通过 → **5% 灰度发布** (微信小程序版本管理 → 发布 → 选 "灰度发布" → 5%) → 监控 30 分钟错误率 < 1%, 30 分钟内无 P0 → **逐步放到 100%**
>
> 备份口径: 任意一步出 P0 → 走 [`04-rollback-plan.md`](./04-rollback-plan.md) 的 **回退到旧版本** 一键操作 (微信小程序后台 → 版本管理 → 回退); 体验版 / 未审核版本不影响线上, 这是首次正式版才有的 "回退" 路径.

---

## ⚠️ 个人主体版补丁 (2026-04-30 拍板更新)

> **决策**: v1.0.0 用**个人主体**小程序首发, ¥0/年; 后续 DAU 上来再升级企业 (¥300/yr 注册费 + 营业执照).

| 影响项 | 个人版限制 | 本次发版调整 |
|--------|----------|-----------|
| **微信支付** | ❌ 不能开 | VIP 升级按钮**长期保持 stub**, 直到主体升级到企业. 文案改成 "VIP 即将上线" / "敬请期待"; 不要拉起任何真支付 |
| **图片上传** | ❌ 不能挂 | 社区发帖**纯文本**模式; 详情页 / 知识库图片走 BE 静态 (CDN 直链) |
| **服务类目** | ⚠️ 金融业 / 证券**严格禁止** | 提审类目改 **"工具 → 信息查询"** (`01-release-plan.md` §4 + `03-release-notes.md` §A.1 已同步) |
| **UGC 社区** | ⚠️ 监管严格 | 提审备注突出 "三级审核 + 反 spam + 新用户 7d 只读" 已就位; 看具体审核员意见, 必要时**降级社区成纯阅读** (改成只显示 admin 发的精选, 不让用户发) |
| **AI 输出** | ⚠️ 同企业版同合规线 | 红线词过滤已守 (20+ pattern), 不变 |

**对应文档段落**:
- [`01-release-plan.md`](./01-release-plan.md) §1.2 + §6.1 (个人版限制)
- [`02-release-runbook.md`](./02-release-runbook.md) §1.4 (`WECHATPAY_DEV_MODE` 长期 true)
- [`03-release-notes.md`](./03-release-notes.md) §A.1 (类目 = 工具 → 信息查询)
- [`05-pre-release-checklist.md`](./05-pre-release-checklist.md) §G.7 + §H.1 (类目 + 主体)

---

## 关键提示 (避免踩坑)

1. **AppID 已对齐**: `apps/mp/manifest.json` + `apps/mp/project.config.json` + 仓库根 `project.config.json` 三处统一为 `wxe525868b30a43b96`, dev 工具开 `apps/mp/dist/build/mp-weixin/` 自动识别正确 ID
2. **首次发版只发小程序**: H5 / Android / iOS 在 [`docs/RUNBOOK.md`](../../RUNBOOK.md) §部署方案 B 各有路径, 但本工作包**只覆盖小程序**. 其它端单独排
3. **正式发版前必须切实跑过 1 次 staging**: 本地 dev 跑通 ≠ 提审版能跑. 微信小程序对 ``request 合法域名`` 是硬性要求, 体验版起就开始校验 (本地调试可以"不校验合法域名", 提审时这个开关无效)
4. **微信支付 沙箱 vs 生产**: `apps/api/.env.example` 默认 `WECHATPAY_DEV_MODE=true` 是 stub, **生产部署时必须翻成 `false` + 挂证书 + 备案 HTTPS notify_url**, 否则 VIP 升级走支付会失败. 见 [`02-release-runbook.md`](./02-release-runbook.md) §1.4
5. **金融类目过审有红线**: AI 输出严禁 "建议买入 / 满仓 / 必涨 / 稳赚 / 抄底 / 保本" (`spec/06` §6.1). 后端 `tests/test_forbidden_pattern_filter.py` 20+ case 已守, 但**提审前最后一次手测必须再触发一次** "xxx 必涨" 验证后端真挡掉 (见 checklist §合规线)
6. **服务器域名**: 提审前必须在 [mp.weixin.qq.com](https://mp.weixin.qq.com) → 开发管理 → 开发设置 → 服务器域名 加 `https://api.你的域名.com` (request) + `wss://`(若用 ws). 没配的话提审第一步就被打回

---

## 后续维护

- 后续每次发版按 `02-NN-YYYY-MM-DD-release/` 命名, 复制本目录结构改即可
- 发版完成后, 在本目录追加一份 `99-postmortem.md` 写清楚 "这次踩了哪些坑, 下次怎么避免", 用于优化 runbook
- 长期维护的 P0 回归 / BC tracker / utm 审计在 [`../release/`](../release/), 不要复制到本目录, 那边是常驻

---

> 🎯 **vibe coding 收尾原则**: 这套文档是模板, 但每条命令 / 每个数字都对应 `xgzh/` 真实文件. 任何看着不对的, 别犹豫问回我, 不要硬上.
