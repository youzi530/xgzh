<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'

import { useThemeStore } from '@/stores/theme'

onLaunch(() => {
  // FE-S4-004: 冷启动恢复主题; 必须放 onLaunch 而非组件 setup 顶层 —
  // setup 期 H5 documentElement 已 ready, 但 mp-weixin onLaunch 比 setup 顺序更"早",
  // 用户从 storage 切深/浅时, App.vue setup 已挂, 不会重跑 — 只能靠 store init.
  const theme = useThemeStore()
  theme.init()
})
</script>

<style lang="scss">
/* XGZH 全局样式
 *
 * CSS 变量定义同时挂在 ``:root`` (H5) + ``page`` (小程序) 上:
 * - 小程序 wxss 没有 ``:root`` 选择器, 顶层是 ``page``; 只写 ``:root`` 会让所有
 *   ``var(--color-*)`` 在小程序端 fallback 到 ``initial`` (透明/黑), 表现为
 *   "白底白字"、tab 选中后看不见等 UI 灾难。
 * - H5 上 uni-app 把 ``page`` 编译成 ``uni-page-body``, 不是文档根, ``var`` 继承
 *   范围有限 (例如某些 portal/teleport 出来的元素拿不到), 所以 ``:root`` 兜一份。
 *
 * FE-S4-004 暗黑/浅色主题切换:
 * - 默认 dark (现有色板; 落到 ``page, :root`` 上, 任何端不显式 set 都走 dark)
 * - 浅色 通过 ``[data-theme='light']`` (H5 ``<html>``) + ``page.theme-light``
 *   (mp 端 ``page`` 上挂 class) 双轨覆盖
 * - 用户切换时 ``stores/theme.ts`` 同时调 ``documentElement.setAttribute`` (H5)
 *   + ``uni.setNavigationBarColor`` (mp 导航栏); 内容层依赖 CSS 变量自动重算
 */
page,
:root {
  --color-bg: #0b1220;
  --color-surface: #131a2c;
  --color-surface-elevated: #1a2238;
  --color-text: #e2e8f0;
  --color-text-muted: #94a3b8;
  --color-primary: #4f8bff;
  --color-accent: #f6c453;
  --color-success: #22c55e;
  --color-danger: #ef4444;
  --color-border: rgba(255, 255, 255, 0.06);
}

/* ─── 浅色主题 (FE-S4-004) ─────────────────────────────────
 *
 * 设计原则:
 * - bg #f8fafc → 比纯白柔和, 长时间阅读不刺眼
 * - text #0f172a → 与 dark 主题的 text 互为补色, 对比度 16:1 (AAA)
 * - text-muted #64748b → AA 级 4.5:1
 * - surface #fff + 微阴影 (H5 用 box-shadow; mp 静态 border 兜底)
 * - primary #2563eb → 蓝色更深, 浅底上视觉重量足
 * - accent #d97706 → 金黄变橙, 浅底上保持可识别
 * - border 用浅灰色 5% 黑而非透明白
 */
/* QA-S4-002 BC-8 修复:
 * uni-app H5 把 ``page`` 选择器编译为 ``uni-page-body``, 所以上面的
 * ``page, :root { --color-bg: #0b1220 }`` 实际把 *变量* 直接定义在
 * ``uni-page-body`` 上 — 这比从 ``:root`` 继承下来的同名变量优先级高,
 * 导致 ``:root[data-theme='light'] { --color-bg: #f8fafc }`` 改了 ``html``
 * 上的变量但 ``uni-page-body`` 自己的同名变量纹丝不动. 必须再加一条直接
 * 命中 ``uni-page-body`` 的下沉 override (类似 ``html data-theme='light'``
 * 时把 uni-page-body 视作下层).
 *
 * 设计: 同时保留 ``page.theme-light`` 给 mp-weixin 端 (mp ``page`` 不会
 * 被编译, 仍按字面 ``page`` 选择器写; 用 store ``reapply()`` 在 mp 端给
 * page 挂 class — mp v2 增强).
 */
:root[data-theme='light'],
:root[data-theme='light'] uni-page-body,
page.theme-light,
uni-page-body.theme-light {
  --color-bg: #f8fafc;
  --color-surface: #ffffff;
  --color-surface-elevated: #f1f5f9;
  --color-text: #0f172a;
  --color-text-muted: #64748b;
  --color-primary: #2563eb;
  --color-accent: #d97706;
  --color-success: #16a34a;
  --color-danger: #dc2626;
  --color-border: rgba(15, 23, 42, 0.08);
}

page {
  background: var(--color-bg);
  color: var(--color-text);
  font-family:
    -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue', sans-serif;
}

/* FE-S4-004 + QA-S4-002 BC-8 修复:
 * H5 端 uni-app 把 ``page`` 编译为 ``uni-page-body`` 但实际可视 wrapper 是
 * ``uni-page-body``, 因为 uni-app 内部又给 ``uni-page`` / ``html`` / ``body``
 * 各自挂了一份默认 bg, ``page { background }`` 单写过不去. QA-S4-002 实测
 * console: ``uni-page-body-bg=rgb(11,18,32)`` 拉不动主题 — 显式补 H5 选择器.
 *
 * mp 端不会进 H5 条件块, 不影响小程序行为.
 */
/* #ifdef H5 */
html,
body,
uni-app,
uni-page,
uni-page-wrapper,
uni-page-body {
  background: var(--color-bg) !important;
  color: var(--color-text);
}
/* #endif */
</style>
