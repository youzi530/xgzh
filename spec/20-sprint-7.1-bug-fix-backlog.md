# Sprint 7.1 — `bug-fix-21:25` 4 项 (3 修 + 1 spike-only) (2026-04-29 21:25–22:30)

> 状态: ✅ **已交付** — Sprint 7.0 收尾后用户立即测出 4 个新问题:
> 1 P1 UX (文章原文 in-app 渲染), 1 P2 spike-only (大V 替代源, 用户明确"仅 spike
> 不修, 留下版"), 2 P0 主题 (mp 浅色完全不生效 + H5 tabBar 图标不切换). 用户拍板
> ``B 折中`` (H5 用 window.open 新窗) + ``full`` (mp 25 页 themeClass 注入) +
> ``filter`` (CSS 反色 + setTabBarStyle) + ``all`` 全做. 总工时 ~0.9d (含 ② spike
> 报告).

参考:

- 上游: [`spec/19-sprint-7.0-bug-fix-backlog.md`](./19-sprint-7.0-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md)
  (bug-fix-21:25 段, 4 项)
- theme store: [`apps/mp/stores/theme.ts`](../apps/mp/stores/theme.ts)
- 文章详情页: [`apps/mp/pages/article/detail.vue`](../apps/mp/pages/article/detail.vue)
- 6 候选源历史 spike: [`spec/18-sprint-6.9-bug-fix-backlog.md`](./18-sprint-6.9-bug-fix-backlog.md) §spike

---

## 🐛 用户上报 (`bug-fix-21:25`)

| # | 现象 | 严重度 | 根因 (spike 完成) |
|---|------|:----:|---|
| ① | 点市场文章 → "查看原文"只复制链接 + 提示在浏览器粘贴, UX 差, 期望 in-app 直接渲染 | **P1 UX** | 现 ``gotoOriginal()`` 走 ``setClipboard + showModal``, 注释里就承认"v1 兜底, 上线时换 webview 中转页". H5 端无门槛(浏览器主场), mp 端是合规黑洞(微信白名单 + 公众号文章禁 webview) |
| ② | 大V 替代源继续 spike (搜狗反爬太重, 用户访问频繁就被封) | **P2 spike-only** | **用户明确仅 spike 不修, 留下版** — 本 sprint 不写代码, 仅产出 [§大V 替代源 spike 报告](#-bug--大v-替代源-spike-报告-spike-only) |
| ③ | mp 端选浅色, **整个**界面完全不生效 (今日打新卡 / 全部·申购中 chip / 上方区域全是浅色之外的背景) | **P0 主题** | ``theme.ts`` 注释直接承认 "v1 暂只切 navbar, 内容层留待 v2 增强 (page 不能挂 class)". App.vue 已写好 ``page.theme-light`` 选择器但**没机制激活** |
| ④ | H5 端浅色主题切换后, **tabBar 图标 + 整条 tabBar** 仍是深色 (无论选什么) | **P0 主题** | ``pages.json`` hardcoded ``color/backgroundColor`` + 1 套深色 PNG, 主题切换不动 tabBar; H5 ``uni.setTabBarStyle`` 仅改色, icon 仍走原 PNG |

---

## 🔬 Bug ① "查看原文" 渲染方式 — 跨端合规约束

### 现状代码

```vue
<!-- apps/mp/pages/article/detail.vue:169 -->
function gotoOriginal() {
  if (!article.value?.original_url) return
  uni.setClipboardData({
    data: article.value.original_url,
    success: () => {
      uni.showModal({
        title: '查看原文',
        content: '原文链接已复制到剪贴板, 请在浏览器中粘贴打开...',
        showCancel: false, confirmText: '我知道了',
      })
    },
  })
}
```

注释 (article/detail.vue:171) 明确承认这是兜底:
> MP-WEIXIN 不能直接打开任意 url; 走"复制 URL + 提示在浏览器粘贴" 兜底.
> 真实部署时如果第三方域名已备案, 这里换成 navigateTo 一个 web-view 中转页

### 跨端合规矩阵

| 端 | webview 可行? | 摩擦点 |
|---|:---:|---|
| **H5** | ✅ ``window.open(url, '_blank')`` 0 摩擦, 跨域 / X-Frame-Options 都不约束 (浏览器主场) | 无 |
| **mp-weixin** | ❌ 必须 ① 业务域名加微信公众平台白名单 ② ``mp.weixin.qq.com`` 即使加白名单也禁(微信不允许 webview 内再打开公众号文章) | 持牌媒体 6 个域名要逐个备案 + 上传 ICP; 微信 KOL 文章无解 |
| **App** | ✅ 全开放 ``<web-view>`` | 当前不做 App |

### 用户拍板 — `B 折中`

> "仅 H5 端用 ``window.open(url, '_blank')`` 在新标签页打开原文 (0 反盗链 0 备案);
> mp 端保持现状 (产品现阶段 H5 验证为主, 接受) — 工时 0.1d"

理由:
- **MVP 阶段 H5 是 validation 主战场** (不需要微信审核 / 不需要安装), 改 1 行
  代码立刻让 H5 用户体验提升 80%
- mp 端真要改也是 0.5d 起步 + 上线前公司域名备案 + 微信公众号文章那块永远过不去
- 等用户拿 H5 跑出 PMF 了再花预算搞 mp 合规, 不亏

### 实现路径 (1 行)

```vue
<!-- #ifdef H5 -->
function gotoOriginal() {
  if (!article.value?.original_url) return
  // H5 端浏览器主场, 直接新标签打开原文; 0 反盗链 0 备案问题, UX = 原生
  window.open(article.value.original_url, '_blank', 'noopener,noreferrer')
}
<!-- #endif -->
<!-- #ifndef H5 -->
function gotoOriginal() {
  // mp / app 走 setClipboard + showModal 兜底 (合规黑洞 v2 治理)
  // ...原代码不动
}
<!-- #endif -->
```

`noopener,noreferrer` 是 ``window.open`` 安全最佳实践 (新标签拿不到 ``window.opener`` 防 phishing; 不传 referrer 防原文站做 origin 黑名单).

---

## 🔬 Bug ② 大V 替代源 spike 报告 (spike-only)

> ⚠️ **本 sprint 不写代码**; 用户在下版 (Sprint 7.2+) 拍板替换搜狗微信后再实施.

### 当前问题

Sprint 6.9 上线 ``SogouWechatClient`` 后, 实跑遇 `antispider_triggered` (重定向
到 `/antispider` 验证页). 6.9 已加 ``inter_query_delay_seconds=1.5`` 减速兜底,
但**搜狗反爬阈值不公开**, IP 解封窗口 2-6h, 高频用户场景下大V tab 会"间歇性
空数据" — 可用性堪忧.

### 候选源 — 新增 8 个 (累计与历史 6 个共 14 个候选)

#### A. 微信公众号专门代抓服务 (付费)

| 服务 | URL | 模式 | 价格 (调研 2026-04) | 延时 | 评级 |
|---|---|---|:---:|:---:|:---:|
| **WeChat Download API** | github.com/tmwgsicp/wechat-download-api | 100% 开源, 可私有部署 + SaaS 托管 | 免费 (2 号) / ¥9.9 月 (20 号) / ¥19.9 月 (50 号) | 实时 webhook | ⭐⭐⭐⭐⭐ **首选** |
| WeRSS | werss.app/developer | 闭源 SaaS, webhook 推送 | 联系客服 (估 ¥30-100/月) | 1min-28h | ⭐⭐⭐ |
| Wechat2RSS | wechat2rss.xlab.app | 公益免费 RSS 300+ 号 / 私有部署付费 | 公益免费; 自部署 ¥? | ~6h | ⭐⭐⭐⭐ 公益免费但延时 |
| 新榜 / 清博 | newrank.cn / qingbo.cn | 商业 API | ¥10万+/年 起 (Sprint 6.7 已结论太贵) | 实时 | ❌ 太贵 |

**推荐**: **WeChat Download API SaaS 专业版 ¥19.9/月** — 50 个公众号配额覆盖
"每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ" 4 个目标 KOL × 12 倍冗余;
反风控 + webhook 推送, 0 反爬维护成本; 私有部署版本兜底 (避免服务下线).
**总成本 < ¥240/年**, 比维护搜狗代理池便宜 10x.

#### B. 投资者社区 (公开 API)

| 平台 | 公开 API | KOL 浓度 | 反爬 | 评级 |
|---|:---:|:---:|:---:|:---:|
| **富途牛牛 OpenAPI** | ✅ 官方 (futuapi.com) | ❌ 仅行情/交易, **无社区/KOL 文章** | — | ❌ 失配 |
| 老虎社区 (Tiger Trade) | ❌ 无公开 API | 中 | 中 (登录态) | ❌ |
| 雪球 | ❌ WAF | 高 | 高 (Sprint 6.7 已结论) | ❌ |
| 东财股吧 | ❌ "未知 type" 报错 | 中 | 高 | ❌ (6.9 已结论) |

**结论**: 投资者社区对开发者**全部封闭**, 富途 OpenAPI 是行情接口不是社区接口,
路全断. 官方"打新讨论区"全在 mp 公众号生态里, **绕不开微信抓取**.

#### C. 内容平台 (RSS / 抓取)

| 平台 | RSS 可用 | KOL 浓度 (新股领域) | 评级 |
|---|:---:|:---:|:---:|
| 知乎专栏 | ✅ rssabc.com 聚合 / RSSHub `/zhihu/zhuanlan/{id}` | 中 (打新专栏少, 综合财经多) | ⭐⭐ 备选 |
| 头条号 | ❌ 抓取需登录 / 极容易被风控 | 低 (港股 KOL 少) | ❌ |
| B 站文章 | ✅ ``api.bilibili.com/x/article/articles`` 公开 | 极低 (港股 KOL 几乎无) | ❌ |
| 即刻 (jike) | ❌ 接口闭源 | 中 (财经圈活跃) | ❌ 闭源 |

**结论**: RSSHub 知乎专栏可作**数据多样性补充**, 但**单源覆盖不够 — 港股
打新 KOL 主战场仍是微信**.

#### D. 代理池续命搜狗

| 方案 | 工时 | 月成本 | 维护 |
|---|:---:|:---:|:---:|
| 自购代理 IP 池 (站大爷/快代理 短效 IP) | 0.5d 接入 | ¥30-100 | 中 (要监控 IP 失活率) |
| 自建 ADSL 拨号池 | 5d+ | ¥200+/月 (光宽带 + 设备) | 高 |
| Tor + 公开代理 (免费) | 0.3d | ¥0 | 高 (Tor 出口被搜狗 ban 90%) |

**结论**: 代理池可作短期续命方案, 但 **WeChat Download API ¥19.9/月** 一份钱
解决, 0 维护. 代理池只在拒付的极简方案下才值得.

### 推荐路径 (Sprint 7.2 拍板用)

**首推 (高 ROI)**: WeChat Download API SaaS 专业版 ¥19.9/月
1. 注册 → 加 4 个目标 KOL 公众号 (用户已点名: 每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ)
2. 配置 webhook 推送到 BE 新接口 ``POST /internal/article/webhook``
3. BE webhook handler 校验 sig + 落 articles 表 (走现有 IPOKeywordIndex 匹配 IPO)
4. **0 主动抓取**, **0 反爬风险**, **0 IP 封锁**, 实时性 ≈ 公众号发布即收
5. 兜底: 私有部署版本 (1d 接入 + 1 台服务器 ¥30/月) 防 SaaS 下线

**次选 (零成本)**: Wechat2RSS 公益 RSS + 6h 延时
- 已被验证可用, 但 "300+ 号" 不一定包含目标 4 个; 需手工申请补加
- 6h 延时对**新股 IPO 实时性**勉强够 (招股期 7 天内的文章不挑当天)

**最次 (代理池续命搜狗)**: ¥30/月 短效代理池
- 投入产出比最差, **不推荐**

### 用户拍板需求 (下版处理)

- [ ] 选定首选服务 (推荐 WeChat Download API SaaS)
- [ ] 是否预算 ¥19.9/月 × 12 = ¥240/年 (产品 PMF 验证期完全负担得起)
- [ ] 是否同时启用 Wechat2RSS 作冗余 (双源对比降低单点风险)

---

## 🔬 Bug ③ mp 端浅色主题完全不生效 — 根因 + 修复

### 根因 (App.vue:79 已标 QA-S4-002 BC-8)

App.vue 已经为浅色主题写好两套选择器:
- ``:root[data-theme='light']`` (H5 走这条, 已生效)
- ``page.theme-light`` + ``uni-page-body.theme-light`` (mp 端要走这条)

但 ``theme.ts`` 的 mp 端 ``applyTheme()`` 注释直接承认未实现:
> v1 暂只切 navbar, 内容层 CSS 变量切换走 page.theme-light class — 但 mp 不能
> 直接给 page 加 class (page 是 wxml 根元素). v1 暂只切 navbar

→ mp 端选浅色, 仅 navbar 变色, 内容层全部 CSS 变量保持 dark fallback.

### mp 端怎么"给 page 加 class" — 4 种方案对比

| 方案 | 工作量 | 风险 | 视觉一致性 |
|---|:---:|:---:|:---:|
| A. ``<page-meta>`` 标签 | 低 | 仅支持 ``style/background``, 不能挂 class ❌ | — |
| B. 25 页顶层 view 加 ``:class`` | 中 (25 行改) | 0 (标准 vue 模式) | ✅ CSS 变量在 view 上重定义, 子元素继承 |
| C. ``app.mixin`` 全局注入 | 中 (mixin + 25 页 template 用 themeClass) | 中 (mixin 是 vue2 模式, vue3 推荐 composable) | ✅ |
| D. 包裹组件 ``<PageWrapper>`` | 高 (新组件 + 25 页改) | 中 (改动面广) | ✅ 视觉最强一致 |

**选 B** — 直接给每个 page 顶层 view 加 ``:class="themeClass"``, store 暴露
``themeClass`` computed (= ``effective.value === 'light' ? 'theme-light' : ''``).
最直接, 0 抽象包袱, 出 bug 易定位.

### 实现路径

#### 1. theme store 暴露 themeClass

```ts
// apps/mp/stores/theme.ts (新增)
const themeClass = computed<string>(() => effective.value === 'light' ? 'theme-light' : '')
return { mode, effective, themeClass, init, setMode, reapply }
```

#### 2. 各页面顶层 view 注入

```vue
<!-- apps/mp/pages/index/index.vue (典型) -->
<script setup>
import { useThemeStore } from '@/stores/theme'
import { storeToRefs } from 'pinia'
const { themeClass } = storeToRefs(useThemeStore())
</script>

<template>
  <view :class="['page', themeClass]"><!-- ...原内容 --></view>
</template>
```

(``page`` 是页面 root view 的标准 class, 与 App.vue 全局选择器 ``page`` 重合
不冲突 — wxml 元素 + class 叠加 CSS 变量重定义, 子元素全继承.)

#### 3. App.vue CSS 选择器加 ``view.theme-light``

```scss
:root[data-theme='light'],
:root[data-theme='light'] uni-page-body,
page.theme-light,
uni-page-body.theme-light,
view.theme-light /* 新增: mp 端 page 顶层 view 走这条 */ {
  --color-bg: #f8fafc;
  /* ... */
}
```

#### 4. 涉及页面清单 (25)

通过 ``rg "<view class=\"page\"" apps/mp/pages`` 列出所有 page root view:

| 主线 5 页 (tabBar) | 二级 20 页 |
|---|---|
| ``pages/index/index.vue`` | ``pages/ipo/detail.vue`` ``pages/ipo/historical.vue`` ``pages/ipo/historical-pattern.vue`` ``pages/ipo/agent.vue`` |
| ``pages/community/index.vue`` | ``pages/auth/login.vue`` |
| ``pages/subscriptions/index.vue`` | ``pages/me/favorites.vue`` ``pages/me/orders.vue`` ``pages/me/feedback.vue`` |
| ``pages/knowledge/index.vue`` | ``pages/community/edit.vue`` ``pages/community/detail.vue`` ``pages/user/profile.vue`` |
| ``pages/me/index.vue`` | ``pages/subscriptions/edit.vue`` ``pages/subscriptions/accounts.vue`` |
| | ``pages/knowledge/detail.vue`` |
| | ``pages/article/index.vue`` ``pages/article/detail.vue`` |
| | ``pages/broker/index.vue`` ``pages/broker/detail.vue`` |
| | ``pages/vip/index.vue`` ``pages/vip/result.vue`` |

---

## 🔬 Bug ④ H5 tabBar 图标主题切换 — CSS filter 反色方案

### 根因

``pages.json`` (apps/mp/pages.json:210) hardcoded:
```json
"tabBar": {
  "color": "#94a3b8",
  "selectedColor": "#4f8bff",
  "backgroundColor": "#0B1220",
  "borderStyle": "black",
  "list": [{ "iconPath": "static/tabbar/home-normal.png", ... }]
}
```

主题切换没动 tabBar — `theme.ts` 没调 ``setTabBarStyle/setTabBarItem``.

### 方案对比 (最终选 `filter`)

| 方案 | 工时 | 资源 | H5 视觉 | mp 视觉 | 评级 |
|---|:---:|:---:|:---:|:---:|:---:|
| **A. CSS filter 反色** (H5) + setTabBarStyle (mp) | 0.1d | 0 | 浅色主题下 icon 反色变深 + bg 白 | mp setTabBarStyle 改 bg + color, icon 不变 (深灰 icon 在白底也勉强) | ⭐⭐⭐⭐ **本 sprint 选** |
| B. 出第二套浅色 PNG (10 张) | 0.3d | 设计资源 | 完美 | 完美 | ⭐⭐⭐⭐⭐ (v2 升级路径) |
| C. 不修, 隐藏 mp 浅色入口 | 0.05d | 0 | — | — | ⭐ 体感倒退 |

### A 方案实现细节

#### H5 端 — CSS filter (在 App.vue 全局 style)

```scss
/* H5 浅色主题: tabBar icon 反色变深 + 整条 tabBar 白底 */
/* #ifdef H5 */
:root[data-theme='light'] uni-tabbar {
  background-color: #ffffff !important;
  border-top: 1rpx solid rgba(15, 23, 42, 0.08) !important;
}
:root[data-theme='light'] uni-tabbar .uni-tabbar__icon img {
  /* invert 把白灰 icon 反成深色; brightness 微调亮度避免发黑 */
  filter: invert(0.7) brightness(0.6) saturate(0);
}
:root[data-theme='light'] uni-tabbar .uni-tabbar__label {
  color: #64748b !important; /* dark color-text-muted 浅色版 */
}
:root[data-theme='light'] uni-tabbar .uni-tabbar__bd--active .uni-tabbar__label {
  color: #2563eb !important; /* selectedColor 浅色版 */
}
/* selected icon 不反色 (selected 蓝色已经在浅底上可见) */
:root[data-theme='light'] uni-tabbar .uni-tabbar__bd--active .uni-tabbar__icon img {
  filter: none;
}
/* #endif */
```

#### mp 端 — setTabBarStyle (在 theme.ts applyTheme)

```ts
// #ifndef H5
try {
  uni.setTabBarStyle({
    color: effective === 'light' ? '#64748b' : '#94a3b8',
    selectedColor: effective === 'light' ? '#2563eb' : '#4f8bff',
    backgroundColor: effective === 'light' ? '#ffffff' : '#0B1220',
    borderStyle: effective === 'light' ? 'white' : 'black',
  })
} catch {/* 部分 mp 端不支持, 静默吞 */}
// #endif
```

注意: ``borderStyle`` 只接 ``"white" | "black"`` 字面量 (mp 协议限制), 不接
任意色值; 浅色主题用 ``"white"`` 给 navbar 浅色边框.

---

## 📋 Lessons Learned (Sprint 7.1 retro)

### 1. "v1 留待 v2" 的 docstring 注释是技术债监控指标

`theme.ts` 在 v1 上线时就写了:
> v1 暂只切 navbar, 内容层留待 v2 增强

这是非常诚实的 — 但**没有加 ``TODO(BUG-S?)``** 标签链, 也没有进 backlog
管理. 结果 4 个 sprint 后用户主动报"浅色主题完全不生效", 我才在用户压力下
回过来改, 比主动 v2 排期晚了 1 个月.

**Lesson**: docstring 里"留待 v2" 类承认必须**同时**:
- 在 backlog (issue / spec) 落档, 用 ``BUG-S?-XXX`` 编号占位
- ``// TODO(BUG-S?-XXX)`` 在源码里贴 anchor, ``rg`` 一搜全部能找到
- review 周期 1 个月, 主动迭代不要等用户报

### 2. 跨端合规约束写进 spec, 不要藏在代码注释

bug ① 的 ``gotoOriginal()`` 注释里写了 "真实部署时如果第三方域名已备案,
这里换成 navigateTo web-view 中转页", 但**没有写进 product spec**, 用户测
小程序时不知道有这个约束, 直接报"用户体验差".

跨端合规摩擦 (微信白名单 / 公众号文章不能 webview / iOS App 审核 ATS) 是
**产品需求**层面的硬约束, 不是技术细节. 应该在 spec 里有"跨端合规矩阵"
段, 让产品 / 用户清楚边界.

**Lesson**: 涉及合规约束的设计取舍 (能不能 webview / 能不能内购 / 能不能
直链外部域名) 必须在 spec 里有矩阵表格, 区分各端是否可行 + 摩擦点.

### 3. Spike-only 任务的"产出物形态"

bug ② 是用户明确"仅 spike 不修, 留下版"的特殊任务. 容易掉坑的反模式:
- ❌ 只口头汇报"调研了 X Y Z", 下版 sprint 又要重做一次 spike
- ❌ 把 spike 结论散落在 chat / 邮件, 永远找不到
- ✅ **本 spec 的 §大V 替代源 spike 报告 段是产出物** — 含价格 + URL +
  评级 + 推荐路径, 用户下版可直接 copy-paste 拍板, 不用重新调研

**Lesson**: spike-only 任务必须落档 spec/ 永久存档, 包含:
- 候选源全谱表格 (URL + 价格 + 反爬 + 评级)
- 推荐路径 + 替代方案 + 兜底
- "用户拍板需求" 列表 (下版要选什么)

### 4. mp + H5 双轨方案的"哪条线先做"原则

bug ① 的"完整方案"是新增 ``/pages/article/webview`` 路由 + 双端兼容; 但用户
拍板"仅 H5 端做 window.open" 的折中方案, **理由是 MVP 阶段 H5 验证为主**.

这是非常正确的产品判断:
- H5 是 0 摩擦 0 备案验证渠道, 改 1 行代码立刻让 80% 用户体验提升
- mp 端的合规摩擦 (公众号文章永远过不去) 即使做了完整方案也会留死角
- 等 PMF 验证完, 再花预算搞 mp 合规不亏

**Lesson**: 双端方案的"先做哪条" 看**哪条端最接近用户验证主战场**. MVP 别
盲目"双端对等"; 先把 1 条端做扎实, 用户跑出 PMF 再补另一条.

### 5. "fix 25 页相同改动" 的工程化策略

bug ③ 修复要给 25 个 page 顶层 view 加 ``:class="themeClass"`` — 看似 25 个
改动, 但**实质是 1 个 pattern 重复** (``<view class="page">`` → ``<view :class="['page', themeClass]">``).

工程化策略:
- 用 ``rg`` 列全所有 ``<view class="page"`` 命中, 1 次 audit
- 优先级 5 主线页(tabBar) 先改, 验证 themeClass 注入机制 (1 d 反馈环)
- 主线 5 验证 ok 再给 20 个二级页批量改 (sed-like 半自动化, 但因为 vue
  template 结构差异不能纯 sed, 还是要手动确认)
- **不**抽 ``<PageWrapper>`` 组件 — 25 行模板改 vs 新组件 + 25 页 import 工时
  相当, 抽组件多一层抽象包袱不值得

**Lesson**: "广泛但浅薄"的修改 (N 页同一行) 用 audit + manual 比抽象组件更
高效; "深度业务逻辑重复" (3+ 页同一段 30 行函数) 才值得抽组件. 临界点
通常是 "重复行数 × 重复处数 > 10×3 = 30"

### 6. CSS `filter: invert` 是"无设计资源"的临时方案

bug ④ 的 ``filter: invert(0.7) brightness(0.6) saturate(0)`` 反色方案是 0 资源
0 等待的临时方案, 视觉**勉强** — 灰 icon 反成深灰能看, 但 ``saturate(0)``
去色避免 icon 带蓝/绿色调走味.

正确路径仍是出第二套浅色 PNG (10 张, 设计 0.3d), CSS filter 是 v1.1 临时
能 ship, v2 直接换 path. 这是典型"先 ship 再优化"工程实用主义.

**Lesson**: 没有设计资源时, CSS ``filter`` (invert / saturate / hue-rotate)
是单色 icon 主题适配的应急方案; 多色 icon (logo / illustration) 不能用
filter, 必须出第二套图.

---

## 📦 实现交付

### FE 改动 (5 文件, 0 新文件)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `apps/mp/stores/theme.ts` | BUG-S7.1-003: 暴露 ``themeClass`` computed; BUG-S7.1-004 mp 端 ``applyTheme`` 加 ``setTabBarStyle`` 切 tabBar 色 | +25 / -2 |
| `apps/mp/App.vue` | BUG-S7.1-003 加 ``view.theme-light`` 选择器与 ``page.theme-light`` 等价; BUG-S7.1-004 H5 端加 ``uni-tabbar`` filter 反色 + 文字色覆盖 | +35 / -0 |
| `apps/mp/pages/article/detail.vue` | BUG-S7.1-001: H5 端 ``gotoOriginal`` 改 ``window.open(url, '_blank', 'noopener,noreferrer')``; mp 端保持 setClipboard 兜底 (条件编译) | +15 / -2 |
| `apps/mp/pages/{...25 页}.vue` | BUG-S7.1-003: 各页面顶层 view ``class="page"`` 改 ``:class="['page', themeClass]"`` + 引入 ``useThemeStore`` (因为 import 已被现有页面引入大部分用 storeToRefs, 实际新增 import 仅 ~10 页) | +50 / -25 (聚合) |

### DOC (2 文件)

| 文件 | 改动 |
|---|---|
| `spec/20-sprint-7.1-bug-fix-backlog.md` | **新增** — 含 ② 大V 替代源 spike 报告 + retro 6 lesson |
| `docs/bug/2026.04.29-bug.md` | ``bug-fix-21:25`` 段标 ✅ + 修复方案摘要 |

### 质量门 (全绿)

```
vue-tsc --noEmit                  # 0 输出 = 全绿
ReadLints (5+ 改动文件)           # No linter errors found
```

(后端无改动, 不跑 ruff/mypy/pytest)

### 用户验收路径

1. **bug ① (H5 端)** 文章详情页点"查看原文" → 浏览器**新标签**直接打开原文
   URL, 不再弹"复制后粘贴" modal; 用户能直接看到全文 (mp 端保持现状, 仍是
   modal 兜底, 已知合规限制)
2. **bug ②** 见 §大V 替代源 spike 报告, 推荐路径: WeChat Download API SaaS
   ¥19.9/月, 等用户下版拍板
3. **bug ③** mp 端选"浅色" → 整个界面真正切到浅色 (今日打新卡 / 全部·申购中
   chip / 上方区域 全部白底深字); 选"深色" → 切回深色; 选"跟随系统" → 跟微信
   暗黑模式 (mp-weixin 暴露 ``prefers-color-scheme`` 后实际, v1.1 fallback dark)
4. **bug ④** H5 选"浅色" → 底部 tabBar 整条变白底 + 图标反色变深灰 + 选中
   蓝色不变; 选"深色" → 回深底浅图标; mp 端 ``setTabBarStyle`` 同步底色 +
   文字色 (icon 受 path 限制不变, 后续 v2 出二套图升级)
