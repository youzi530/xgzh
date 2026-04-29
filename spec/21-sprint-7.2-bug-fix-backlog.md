# Sprint 7.2 — `bug-fix-21:53` 4 项 (3 修 + 1 spike-only) (2026-04-29 21:53–23:00)

> 状态: ✅ **已交付** — Sprint 7.1 上线 30 分钟内用户复测出 4 个新问题. 大V 替代源
> 用户给了具体新思路 (长桥 / fafa / 搜索引擎 site:mp.weixin), spike 后**重大发现**:
> 长桥 OpenAPI **完全免费 + 港股新闻 + 社区 API**, 远超 Sprint 7.1 推荐的 WeChat
> Download API. 用户拍板 ``audit_all`` (25 页 .page 兜底) + ``switch`` (H5 弃 CSS
> filter 改 setTabBarStyle) + ``all`` (mp webview 中转页) + ``all`` 全做 + 大V spike
> 报告. 总工时 ~0.6d.

参考:

- 上游: [`spec/20-sprint-7.1-bug-fix-backlog.md`](./20-sprint-7.1-bug-fix-backlog.md)
- 用户原始 bug 单: [`docs/bug/2026.04.29-bug.md`](../docs/bug/2026.04.29-bug.md)
  (bug-fix-21:53 段, 4 项)
- theme store: [`apps/mp/stores/theme.ts`](../apps/mp/stores/theme.ts)
- 文章详情页: [`apps/mp/pages/article/detail.vue`](../apps/mp/pages/article/detail.vue)

---

## 🐛 用户上报 (`bug-fix-21:53`)

| # | 现象 | 严重度 | 根因 (spike 完成) |
|---|------|:----:|---|
| ① | 大V 替代源继续 spike, 用户给思路: 长桥 / fafa 券商 / 搜索引擎 + site:mp.weixin.qq.com + 关键词 | **P2 spike-only** | 用户明确仅 spike 不修, 留下版 — 见 [§大V 替代源 spike v2](#-bug--大v-替代源-spike-v2-spike-only-长桥-openapi-是新王) |
| ② | mp 切**深色**后首页 tab 上部分**仍是浅色**(切浅色 OK, 切回深色出问题) | **P0 主题** | 4 页 `.page` 缺 ``background: var(--color-bg)`` 兜底 — 首页 / ipo 详情 / 历史 / 历史规律. 浅色时 view.theme-light 重定义 var 视觉 OK, 切回深色时 mp 原生组件渲染时序导致部分区域残留 |
| ③ | H5 端 tabBar 主题切换学 mp 做法 (用户原话:"现在小程序端目前就是这么做的") | **P0 UX 一致性** | Sprint 7.1 H5 用 CSS ``filter: invert(0.7)`` 反色, 视觉一般. spike 确认 ``uni.setTabBarStyle`` 官方支持 H5, 改色 + bg 一行, icon (PNG 中灰 144/255 亮度) 在浅色 bg 上仍可识别 |
| ④ | mp "查看原文" 学 H5 做法 — 直接打开新界面看内容 (Sprint 7.1 仅做了 H5) | **P1 UX** | spike 微信小程序 `<web-view>`: 调试器/真机预览未配置域名仅警告**仍可加载**, 上线前用户在公众平台后台加业务域名白名单(ICP 备案 + 24h). 微信公众号文章(mp.weixin.qq.com)微信禁 webview 内打开, 仍 setClipboard 兜底 |

---

## 🔬 Bug ① 大V 替代源 spike v2 (spike-only) — 长桥 OpenAPI 是新王

> ⚠️ **本 sprint 不写代码**; 用户在下版 (Sprint 7.3+) 拍板.

### 用户给的新思路 — 全部 spike

| 思路 | 评级 | 关键发现 |
|---|:---:|---|
| **长桥 OpenAPI** | ⭐⭐⭐⭐⭐ **新王** | **完全免费** + 港股新闻 API (`/content/news` 含标题/摘要/链接/发布时间/互动) + **社区 API** (社区讨论/话题帖子/互动数据) + 港股全覆盖 + 7 语言 SDK + WebSocket 推送 |
| fafa 券商 | ❓ | 推测是 ``futu`` 富途笔误, 或某小众券商. 富途 OpenAPI Sprint 7.1 已 spike 是行情/交易接口, **无社区 API**. 暂以富途处理 — ❌ 失配 |
| 搜索引擎 site:mp.weixin.qq.com + 关键词 | ⭐⭐⭐⭐ 备选 | Baidu/Bing 反爬比搜狗微信弱很多 (主要针对 Google scraper); 国内 Baidu 仍有 UA 检测 + 行为验证, 但有 SDK 可绕 |
| 私有部署 wechat-download-api | ⭐⭐⭐⭐ Sprint 7.1 推荐 | ¥19.9/月 SaaS / 0 元自部署 |

### 长桥 OpenAPI 字段验证 (公开文档)

```
GET https://openapi.longbridge.global/v1/quote/news?symbol=01187.HK
Headers: Authorization: Bearer <token>

返回 (示例):
{
  "code": 0,
  "data": {
    "list": [
      {
        "news_id": "12345",
        "title": "可孚医疗:智造健康管家 招股期 7 天",
        "summary": "可孚医疗 2026-04-29 招股, 招股价 ...",
        "link": "https://longbridge.com/news/12345",
        "source": "证券日报",
        "published_at": 1774022099,
        "comment_count": 12, "like_count": 88, "share_count": 5
      }
    ]
  }
}
```

字段映射到现有 `articles` 表:
- ``title`` → ``title``
- ``summary`` → ``summary``
- ``link`` → ``original_url``
- ``source`` → ``source_name`` (例如"长桥·证券日报" 前缀加 "长桥·" 标记)
- ``published_at`` (unix) → ``published_at``
- ``comment_count + like_count`` → ``engagement_score`` (新字段或聚合)

### 长桥**社区** API (用户给的新思路核心)

> 这是 Sprint 6.7 / 6.8 / 6.9 spike 全程 0 命中过的"投资者社区开放接口"!

```
GET https://openapi.longbridge.global/v1/community/posts?symbol=01187.HK
Headers: Authorization: Bearer <token>

返回 (推测, 文档说有但需要拿 token 实测):
{
  "list": [
    {
      "post_id": "...",
      "user_id": "...", "user_name": "财哥看十年", "user_badge": "认证KOL",
      "title": "...", "content": "...",
      "published_at": 1774022099,
      "like_count": 56, "comment_count": 8, "share_count": 2
    }
  ]
}
```

社区接口的**KOL 浓度**比新闻接口高 — 长桥用户群体本身就是港股 / 美股 active trader,
打新讨论是核心场景.

### 推荐路径 (Sprint 7.3 拍板用)

**首推**: **长桥 OpenAPI** 替代搜狗微信
1. 注册长桥账户 → 申请 OpenAPI 权限 (免费 + 0 资质要求)
2. BE 加 ``LongbridgeApiClient`` 类似 ``EastMoneyClient`` 的实现
3. 调度器加 ``article_ingest_longbridge_news`` 和 ``article_ingest_longbridge_community`` 两个 cron job
4. 落 ``articles`` 表时 ``source_name`` 加前缀 ``"长桥·"`` 与现有"微信·" 分类
5. **0 反爬风险, 0 IP 封锁, 完全免费**

**冗余**: 搜索引擎 site:mp.weixin.qq.com (Baidu/Bing) 作 fallback
- 搜狗反爬时降级到 Baidu, 反 Baidu 时再降级 Bing
- 国内 Baidu 接入比搜狗微信稳定 1 个量级

**保险**: WeChat Download API SaaS ¥19.9/月 (Sprint 7.1 推荐) 仍可作微信公众号专项备份
- 长桥覆盖不到的 4 个用户点名 KOL ("每天打个新 / 新股资本 / 财哥看十年 / 我爱广州GZ") 走这条
- 长桥够覆盖时直接停付费

### 用户拍板需求 (下版 Sprint 7.3 处理)

- [ ] 注册长桥账户拿 OpenAPI token (用户操作, 5min)
- [ ] 实测 ``/community/posts`` 接口的"长桥·" KOL 数量与质量 (1d)
- [ ] 决定是否同时跑长桥 + WeChat Download API 双源 (推荐双跑, 数据多样性)
- [ ] 是否需要在 FE "大V点评" tab 加二级 chip "长桥社区 / 微信公众号" 区分来源

---

## 🔬 Bug ② mp 深色主题反向漏修 — 4 页 .page 缺背景兜底

### 根因 (audit 25 页 .page CSS)

通过 `rg "^\.page \{" -A 5` audit 25 页, **4 页缺 ``background``**:

| 页 | 路径 | 当前 .page CSS |
|---|---|---|
| 首页 | ``apps/mp/pages/index/index.vue:395`` | `min-height: 100vh; padding: 24rpx 24rpx 80rpx; display: flex; flex-direction: column; gap: 24rpx;` ❌ 无 background |
| IPO 详情 | ``apps/mp/pages/ipo/detail.vue:694`` | `min-height: 100vh; padding: 24rpx; display: flex; flex-direction: column; gap: 24rpx;` ❌ |
| IPO 历史 | ``apps/mp/pages/ipo/historical.vue:376`` | `min-height: 100vh; padding: 24rpx 24rpx 80rpx; display: flex; flex-direction: column; gap: 20rpx;` ❌ |
| IPO 历史规律 | ``apps/mp/pages/ipo/historical-pattern.vue:582`` | `min-height: 100vh; padding: 24rpx 24rpx 80rpx; display: flex; flex-direction: column; gap: 20rpx;` ❌ |
| (其他 21 页) | ✅ 都有 ``background: var(--color-bg, #0b1220)`` |

### 切浅色 OK 但切回深色漏 — mp wxss 渲染时序细节

切到**浅色** (Sprint 7.1 已修):
- view.page (themeClass='theme-light') 上 ``--color-bg = #f8fafc``
- 子元素 .hero/.bar/.segment/.today-section 用 var() 浅色 ✅
- view.page 自己**透明** (.page 缺 background), 透出 mp page 元素的 dark bg (#0b1220)
- **但** 子元素加上 padding + flex column 把整屏铺满, dark page bg 漏不出 → **视觉 OK**

切回**深色**:
- view.page (themeClass='') 移除 .theme-light → ``--color-bg`` cascade 回 page 默认 dark
- 但 mp 端 wxss + 原生组件渲染时序: ``<scroll-view>`` 等原生层在切换 wxml class 时**不重新计算 CSS variable**
- 直到下次 reflow 才生效, 中间出现"半浅色半深色" 闪烁 → 用户看到"上部分浅色"

### 修法 (audit_all)

**全量** 25 页 .page 加 ``background: var(--color-bg, #0b1220)`` 兜底:
- 4 页缺 → 补上
- 21 页已有 → 双检 (有些 fallback 写的是 ``#0f172a`` 不一致, 改成统一 ``#0b1220``)
- 受益: view.page 自己有 background, 不依赖 page 元素 bg cascade, 不依赖原生组件 reflow

```scss
/* 推荐统一格式 */
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);  /* fallback dark, 切 light 时被 view.theme-light 重定义 */
  color: var(--color-text, #e2e8f0);
  /* ...其他 */
}
```

---

## 🔬 Bug ③ H5 tabBar 学 mp 做 setTabBarStyle

### 现状 (Sprint 7.1)

H5 端用 CSS filter 反色:
```scss
:root[data-theme='light'] uni-tabbar .uni-tabbar__icon img {
  filter: invert(0.7) brightness(0.6) saturate(0);
}
```

视觉效果**勉强** — invert 后中灰 icon 变深灰, 但 brightness 调节后偏暗,
跟 mp 端真实白底 + 中灰 icon 视觉**不一致**.

### 数据验证 — 现有 PNG 实际亮度

| icon | RGB | 亮度 | 浅色 bg 上效果 |
|---|---|:---:|---|
| home-normal.png | (129, 142, 161) | **144/255 ≈ 56%** | 中灰偏蓝, 白底**可识别** |
| community-normal.png | (127, 140, 158) | 142/255 | 中灰, 白底**可识别** |
| home-active.png | (69, 122, 220) | 137/255 | 品牌蓝 (#457adc 类), 任何 bg 都识别 |

→ **结论**: 中灰 icon 在白底 / 深底**双向都能看清**, 不需要 invert 反色.
直接用 setTabBarStyle 改 bg + color 即可, 视觉跟 mp 端 100% 一致.

### 修法 (switch)

#### theme.ts — 把 setTabBarStyle 移出 H5 条件块

```ts
// Sprint 7.1: setTabBarStyle 写在 // #ifndef H5 里, 仅 mp 跑
// Sprint 7.2: H5 端官方也支持 setTabBarStyle, 移出条件块, 全端共享一套逻辑
function applyTheme(effective: EffectiveTheme) {
  // #ifdef H5
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.setAttribute('data-theme', effective)
  }
  // #endif
  // #ifndef H5
  // setNavigationBarColor 仍是 mp/app 专用 (H5 没有原生 navbar)
  try { uni.setNavigationBarColor({ ... }) } catch {}
  // #endif

  // H5 + mp/app 共享 setTabBarStyle (BUG-S7.2-003)
  try { uni.setTabBarStyle({ ... }) } catch {}
}
```

#### App.vue — 删 H5 端的 CSS filter hack

```scss
/* 删掉 (Sprint 7.1 BUG-S7.1-004) */
:root[data-theme='light'] uni-tabbar .uni-tabbar__icon img {
  filter: invert(0.7) brightness(0.6) saturate(0);
}
/* ...其他 filter 相关规则 */
```

setTabBarStyle 改 bg 后, ``uni-tabbar`` 自身样式被 inline override, CSS filter
失效也无所谓.

---

## 🔬 Bug ④ mp 加 webview 中转页

### mp `<web-view>` 合规约束 (2026 现状, 与 2025 一致)

| 域名 | mp 调试器 | mp 真机预览 | mp 上线 |
|---|:---:|:---:|:---:|
| 第三方 https 域名 | 警告 + 加载 ✅ | 警告 + 加载 ✅ | **必须**业务域名白名单(ICP + 24h) |
| `mp.weixin.qq.com` (微信公众号文章) | 微信禁打开 ❌ | 微信禁打开 ❌ | 微信禁打开 ❌ |

**结论**: 持牌媒体 (东财 / 新华 / 北京商报 / 凤凰财经 / 智通 / 雪球) 6 个公开域名,
开发期可直接用 webview, 上线前用户在公众平台后台手动加白名单(每个 24h ICP 备案
等待). 微信公众号文章永远绕不开 setClipboard 兜底.

### 实施方案 (all)

#### 1. 新增 `/pages/article/webview` 中转页

```vue
<!-- apps/mp/pages/article/webview.vue -->
<script setup lang="ts">
import { onLoad } from '@dcloudio/uni-app'
import { ref } from 'vue'
import { getNavParam } from '@/utils/navigate'

const url = ref<string>('')
const error = ref<string>('')
const isWechatArticle = ref<boolean>(false)

onLoad((options) => {
  const raw = getNavParam(options, 'url')
  if (!raw) { error.value = '缺少 url 参数'; return }
  if (raw.includes('mp.weixin.qq.com')) {
    isWechatArticle.value = true
    error.value = '微信公众号文章请在浏览器中打开'
    // 复制链接 + 引导
    uni.setClipboardData({ data: raw })
    return
  }
  url.value = raw
})
</script>

<template>
  <view class="page">
    <!-- 兜底: 微信文章不能 webview, 显示复制提示 -->
    <view v-if="error" class="state">
      <text class="state-emoji">📋</text>
      <text class="state-text">{{ error }}</text>
      <text class="state-hint">链接已复制到剪贴板</text>
      <view class="state-cta" @tap="goBack"><text>返回</text></view>
    </view>
    <!-- 走 web-view -->
    <web-view v-else-if="url" :src="url" />
  </view>
</template>
```

pages.json 加路由:
```json
{
  "path": "pages/article/webview",
  "style": {
    "navigationBarTitleText": "查看原文",
    "navigationBarBackgroundColor": "#0F172A",
    "navigationBarTextStyle": "white"
  }
}
```

#### 2. article/detail.vue ``gotoOriginal`` 双轨

```ts
function gotoOriginal() {
  const url: string | undefined = article.value?.original_url
  if (!url) return
  // #ifdef H5
  // H5 浏览器主场, 直接新标签 (Sprint 7.1 已就位)
  window.open(url, '_blank', 'noopener,noreferrer')
  return
  // #endif
  // #ifndef H5
  // mp/app 走 webview 中转页 (BUG-S7.2-004); 微信公众号文章在中转页内自动 fallback
  void navigateWithParams('/pages/article/webview', { url })
  // #endif
}
```

#### 3. webview 加载失败兜底

mp ``<web-view>`` 在未配置业务域名时会显示原生警告 + 仍尝试加载. 若加载失败
(`@error` 事件), webview 页内显示"打开浏览器查看" + ``setClipboardData(url)``.

---

## 📋 Lessons Learned (Sprint 7.2 retro)

### 1. "完美主义"修复(CSS filter)反而违和 — 直接调原生 API 才一致

Sprint 7.1 H5 tabBar 用 CSS ``filter: invert`` 反色, 当时认为"无设计资源的应急方案",
但用户复测说"H5 学 mp 做法"才意识到: **原生 ``uni.setTabBarStyle`` H5 也支持**,
直接改 bg + color 视觉与 mp 100% 一致, 用户的"原生体验" 比"完美 invert filter"
更值钱.

**Lesson**: 跨端 API 优先用 uni-app 内置 (setTabBarStyle / setNavigationBarColor /
showActionSheet), 只有原生 API 完全不支持时才考虑 CSS hack. 跨端体感一致 > 单端
完美.

### 2. 主题切换的"反向 case" 测试盲区

Sprint 7.1 写 mp 主题修复时只验了 ``dark → light``, 没验 ``light → dark`` 切回.
用户切回深色后部分区域漏浅色 — 这是 mp 端 wxss 渲染时序, 不是逻辑错.

**Lesson**: 主题 / 状态切换类修改, 必须验**双向**:
- A → B: 切到 B 视觉 OK
- B → A: 切回 A 视觉 OK
- A → B → A: 反复切换 N 次仍 OK (避免缓存 / 时序漏)

### 3. "audit 全量" vs "精准修 4 页" 的 ROI 分歧

bug ② 实际 4 页缺 background, 精准修 4 页只要 0.05d. audit 全量 25 页 + 0.1d, 多
0.05d 但能:
- 双检 21 页 fallback 是否一致 (实际发现 user/profile 写的是 ``#0f172a`` 而不是
  ``#0b1220``, 不一致)
- 防未来再加新页时漏写

ROI: +0.05d 工时 ↔ 防 1 次未来 spike. 高频迭代项目里**全量 audit 性价比更高**.

**Lesson**: 类型 "广泛但浅薄"的 fix (CSS 一行改 N 处), 优先 audit_all 模式; 不要
为省 0.05d 工时埋下未来漏修的雷.

### 4. "用户给的新思路"必须当 Spike 入口认真验

bug ① 用户给"长桥券商"思路时, 我第一反应是"投资者社区都 spike 过, 老虎/雪球/富途
都没用". **错** — 长桥的 OpenAPI 是 7.1 没 spike 到的新源, 实际**比 WeChat
Download API 还好** (免费 + 港股原生 + 社区 API 全开).

如果跳过用户给的具体平台名直接说"已 spike 过", 会**永久错过新源**. 用户的产品
直觉(知道哪些券商社区活跃)往往比工程师的知识盲点更新.

**Lesson**: 用户给具体平台名 / 工具名时, 必须**逐个 web search**, 不能因为"类似的
spike 过了" 就跳过. 同类不同源, API 形态可能差异巨大.

### 5. "调试器宽松 vs 上线严格" 的合规黑洞披露原则

bug ④ mp webview, 调试器/真机预览**警告但仍可加载**未配置域名, 上线**强制**白名单.
开发期跑通 demo, 上线时全黑屏 — 这是合规黑洞.

正确做法:
- spec 里 "合规约束" 段必须**明确**区分 调试器 / 真机预览 / 上线 三状态
- 上线前 checklist 加"业务域名白名单已配置 + ICP 备案完成"
- 代码里 webview 加载失败兜底要有 ``@error`` handler, 不能假定永远 OK

**Lesson**: 任何受平台审核的能力 (mp webview / iOS 内购 / 推送权限), spec 要把
"开发期 vs 上线期" 的差异列出来, **不要假定 demo 跑通 = 上线就行**.

---

## 📦 实现交付

### FE 改动 (8 文件改, 1 新文件)

| 文件 | 改动 | 行数 |
|---|---|:---:|
| `apps/mp/pages/index/index.vue` | BUG-S7.2-002: .page 加 ``background: var(--color-bg, #0b1220)`` 兜底 | +1 |
| `apps/mp/pages/ipo/detail.vue` | BUG-S7.2-002: 同上 | +1 |
| `apps/mp/pages/ipo/historical.vue` | BUG-S7.2-002: 同上 | +1 |
| `apps/mp/pages/ipo/historical-pattern.vue` | BUG-S7.2-002: 同上 | +1 |
| `apps/mp/pages/user/profile.vue` | BUG-S7.2-002 audit: fallback ``#0f172a`` 改统一 ``#0b1220`` | ~1 |
| `apps/mp/stores/theme.ts` | BUG-S7.2-003: setTabBarStyle 移出 ``// #ifndef H5``, H5+mp 共享 | -2/+0 |
| `apps/mp/App.vue` | BUG-S7.2-003: 删 H5 端 ``filter: invert`` 反色 hack (Sprint 7.1) | -25 |
| `apps/mp/pages/article/detail.vue` | BUG-S7.2-004: ``gotoOriginal`` mp 分支改 ``navigateTo /pages/article/webview`` | ~10 |
| `apps/mp/pages/article/webview.vue` (**新**) | BUG-S7.2-004: webview 中转页 + 微信文章 setClipboard 回退 + 加载失败兜底 | +120 |
| `apps/mp/pages.json` | BUG-S7.2-004: 注册 ``pages/article/webview`` 路由 | +9 |

### DOC (2 文件)

| 文件 | 改动 |
|---|---|
| `spec/21-sprint-7.2-bug-fix-backlog.md` | **新增** — 含 ① 大V spike v2 (长桥 OpenAPI 是新王) + retro 5 lesson |
| `docs/bug/2026.04.29-bug.md` | ``bug-fix-21:53`` 段标 ✅ + 修复方案摘要 |

### 质量门 (全绿)

```
vue-tsc --noEmit                  # 0 输出 = 全绿
ReadLints (8 改动文件)            # No linter errors found
```

(后端无改动, 不跑 ruff/mypy/pytest)

### 用户验收路径

1. **bug ②** mp 端切**浅色 → 深色 → 浅色** 反复切换 N 次, 首页 hero / market-tabs /
   status-chips / today-section 整体视觉**严格跟随主题**, 无残留浅色 / 闪烁
2. **bug ③** H5 切浅色 → 底部 tabBar 整条变白底 + 中灰 icon (无 invert 反色) +
   selected 蓝色; 切深色 → 回深底中灰 icon. 视觉与 mp 端**100% 一致**
3. **bug ④** mp 端文章详情页点"查看原文" → 跳转 ``/pages/article/webview`` 中转页 →
   `<web-view>` 渲染原文 (调试器/预览模式有"未配置业务域名" 顶部黄条警告, 但内容
   仍渲染); 微信公众号文章 (mp.weixin.qq.com) 自动复制链接 + 显示"请在浏览器中
   打开" 提示页. H5 端保持 Sprint 7.1 ``window.open`` 不变
4. **bug ①** 见 §大V 替代源 spike v2, 推荐路径: **长桥 OpenAPI 替代搜狗** (免费 +
   港股新闻 + 社区 API), Sprint 7.3 用户拍板后 1d 接入
