# UTM & 埋点全量审计 (FE-S5-004)

> **范围**：spec/03 §模块四 + spec/07 §1.1 列出的 8 处运营入口
> **目标**：上线前确认 UTM 透传链路在每个入口都打通,运营投放 GA 不丢归因
> **统一工具**：`xgzh/apps/mp/utils/utm.ts`(parse / persist / read / clear,7d TTL)

---

## 1. 透传机制总览

```
[Step 1] 落地页 (任一入口)
    ↓ uni-app 把 query 暴露在 page onLoad / App.onLaunch / App.onShow
[Step 2] App.vue onLaunch + onShow → captureUtmFromQuery(options.query)
    ↓ utils/utm.ts 持久化到 localStorage (key: xgzh.utm.payload, TTL 7d)
[Step 3] 用户继续使用(浏览/试用/登录)
    ↓ 关键动作触发"出口"
[Step 4a] 登录成功 → stores/auth.ts setSession() 自动调
            POST /invite/bind (若 invite_code 存在) → BE-S5-005 邀请关系入库
            完成后 clearUtm() 防重试
[Step 4b] 券商点击 → /api/v1/brokers/{slug}/redirect 在 query 现拼 utm_*
            落 conversion_events.click(BE-S3-008,与 localStorage 解耦)
[Step 4c] VIP 升级 → upgradeModal.source 携带内部归因(quota_banner /
            inline_error / me_page),不依赖 UTM
```

**双层捕获**:`App.vue onLaunch` 兜冷启,`App.vue onShow` 兜热启回前台 +
H5 单页面直接打开。`captureUtmFromQuery` 是 LWW + merge 语义,
`utm_source` 和 `utm_campaign` 来自不同链路时不会互相覆盖。

**TTL = 7d**:与运营推广窗口对齐;>14d 噪音大,<24h 太严苛。

---

## 2. 8 处入口审计

| # | 入口 | UTM 来源 | 透传到哪里 | 状态 | 守护方式 |
|---|------|---------|-----------|-----|---------|
| 1 | 公众号文章 → H5 | `utm_source=wechat&utm_medium=article&utm_campaign=...` | localStorage → 登录后 bindInvite 透传 invite_code | ✅ 已守 | App.vue onShow + auth.setSession hook |
| 2 | 知乎 / 小红书种子文 | 同上,`utm_source=zhihu` / `xiaohongshu` | 同上 | ✅ 已守 | 同 #1(同样走 App.vue 兜底) |
| 3 | 历史 IPO 卡片(站内) | 站内导航,无外部 UTM | N/A — 站内不需要 | ✅ N/A | spec 标"已上线"是指卡片点击转化漏斗,无新埋点需求 |
| 4 | AI 报告 SSE 入口 | 站内导航,无外部 UTM | 内部归因走 chat session id | ✅ N/A | 同 #3 |
| 5 | 券商对比 → 开户跳转 | `utm_campaign=compare_table` / `detail_cta` | URL 现拼 → BE 302 → conversion_events | ✅ 已守 | apps/mp/api/broker.ts buildReferralQuery + e2e (BE-S3-006/008) |
| 6 | VIP 升级 | `upgrade_source=quota_banner` / `inline_error` / `me_page` / `manual` | upgradeModal.source → 内部归因,不写 conversion_events | ✅ 已守 | composables/upgradeModal.ts + FE-S3-004 e2e |
| 7 | 邀请有礼 → 落地页 | `?invite_code=XXX` | localStorage → 登录后 bindInvite | ✅ 已守 | utils/utm.ts + auth.setSession 自动 bind hook |
| 8 | 文章详情(站内站外) | 站外 `utm_source=wechat&utm_content=article_xxx` | localStorage(可选透传未来 conversion_events) | ✅ 已守 | App.vue onShow 兜底 |

> **说明**:#3 / #4 是站内入口(用户已经进入 H5 / 小程序后再点的卡片),
> 不需要 UTM(归因已经由 #1 / #2 / #7 / #8 在落地时落了 localStorage,
> 站内导航不会丢)。spec 把这两处列入"8 处审计点"是要确认"用户从这些卡片
> 点出去之后转化能算回原始 UTM",答案是肯定的(localStorage 会保留 7d)。

---

## 3. 验证步骤(运营冷启演练)

> 每条都需要在 H5 + mp-weixin **双端** 跑过

### 3.1 邀请落地(主路径)

1. 用户 A 在 me 页生成自己的邀请码 `MEY8F3`
2. 分享链接:`https://xgzh.com/#/pages/index/index?invite_code=MEY8F3&utm_source=wechat&utm_campaign=invite_2026q2`
3. 用户 B 点击 → 落首页 → App.vue onLaunch/onShow 捕获 query → localStorage 写入
4. 用户 B 点 "登录" → 完成短信验证码 → setSession() 触发
5. setSession 内部 `_maybeBindInviteFromUtm()` → `POST /invite/bind {code: "MEY8F3"}` → 200
6. clearUtm() → localStorage 清空
7. 用户 A 集到第 3 个邀请 → BE-S5-005 cron 把用户 A 的 VIP 试用 +7d

**验证点**:用户 A 的 me 页 / VIP 页能看到 +7d;BE conversion_events 表
能 GROUP BY utm_campaign='invite_2026q2' 看到点击数(若运营要这维度,
后续接 BE-S5-006 dashboard 可视化)。

### 3.2 公众号文章 → H5(冷启场景)

1. 微信公众号文章末尾跳:`https://xgzh.com/#/pages/article/detail?id=ipo_xxx&utm_source=wechat_oa&utm_medium=article&utm_campaign=monthly_review`
2. 用户点 → H5 onShow 触发 captureUtmFromQuery → localStorage
3. 用户继续浏览 → 点 VIP 升级 → 完成支付
4. (未来)BE 加 `/api/v1/track` 后,前端可在支付成功回调读 utm 一起上报

**当前阶段**:仅做沉淀;未来加 conversion_events 全量上报后 0 改动接入。

### 3.3 券商对比 → 开户跳转(已有 e2e)

1. 用户在列表页 / 详情页点 "立即开户"
2. apps/mp/api/broker.ts `buildReferralQuery({utm_campaign, utm_medium})`
   现拼到 URL → BE 302 → conversion_events.click 入库
3. (与 utm.ts localStorage 链路解耦,已有 BE-S3-008 e2e 守)

---

## 4. 已知漏点 & 后续 Sprint 待办

### 4.1 当前阶段未实现

| 漏点 | 影响 | 何时补 |
|------|-----|-------|
| **没有独立 `/pages/invite/landing`** | 邀请分享链接只能落首页 + query 兜底,无法做"邀请人专属落地体验"(显示邀请人头像 / 奖励文案) | Sprint 6,FE-S6-XXX 如果运营有需求 |
| **`/api/v1/track` 通用埋点端点不存在** | utm_source/medium/campaign 沉淀在前端,只有 invite_code 真正回到 BE | Sprint 6 BE-S6-XXX(配合 dashboard 可视化扩展) |
| **没有 vitest 单测** | utm.ts 的 7d TTL / merge 语义只能靠 vue-tsc + 手测 | Sprint 6 FE-S6-XXX(整体引入 vitest 后补) |
| **Sentry breadcrumb 不带 utm** | 用户出错时定位不到原始流量来源 | OPS-S5-001 已经接 Sentry,后续在 request 拦截器加一行 setTag 即可,< 1h |

### 4.2 监控盲区(可接受)

- localStorage 在小程序冷启后仍然能读到(uni.getStorageSync 是 wx.storage),
  但用户卸载小程序 → 重装,会丢。这个 case 算不可恢复, GA 行业标准也接受。
- 用户登录前 7d 内没绑邀请人(冷却 / 弃用),localStorage 自动清,后续登录
  无法补绑。这是 TTL 的固有代价,与"避免长尾污染"权衡后选了 TTL 短的一边。

---

## 5. 代码索引(给后续维护人)

| 模块 | 路径 | 职责 |
|------|------|------|
| 核心 helper | `apps/mp/utils/utm.ts` | parse / persist / read / clear,7d TTL,DI 友好 |
| 冷启捕获 | `apps/mp/App.vue` `onLaunch` | 小程序场景值(scene)+ query 入库 |
| 热启捕获 | `apps/mp/App.vue` `onShow` | H5 单页直接打开兜底 |
| 邀请绑定 hook | `apps/mp/stores/auth.ts` `setSession` → `_maybeBindInviteFromUtm` | 登录成功自动 bindInvite + clearUtm |
| 券商点击(独立链路) | `apps/mp/api/broker.ts` `buildReferralQuery` | URL query 现拼,不走 localStorage |
| VIP 内部归因 | `apps/mp/composables/upgradeModal.ts` `UpgradeSource` | quota_banner / inline_error / me_page / manual |

---

## 6. 验收清单(✅ 上线前必须勾完)

- [x] `apps/mp/utils/utm.ts` 实现 + 7d TTL
- [x] App.vue onLaunch + onShow 双层捕获
- [x] auth.setSession 自动 bindInvite + clearUtm
- [x] 8 处入口对照表填写(✅ 已守 / ✅ N/A 站内)
- [x] vue-tsc --noEmit 0 错
- [x] 已知漏点 & 后续待办列出
- [ ] 运营在 staging 跑过 §3.1 邀请落地一遍(QA-S5-002 P0 回归覆盖)
- [ ] 运营在 staging 跑过 §3.2 公众号文章落地一遍(QA-S5-002 P0 回归覆盖)
- [ ] BE conversion_events 表 admin/dashboard 可见(BE-S5-006 已实现 → QA-S5-002 验)
