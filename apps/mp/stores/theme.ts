/**
 * 主题切换 Pinia store (FE-S4-004 暗黑/浅色模式).
 *
 * 模式三态:
 * - ``'auto'`` 跟随系统 (H5 走 ``prefers-color-scheme``; mp 没暴露则 fallback dark)
 * - ``'dark'`` 强制深色 (默认)
 * - ``'light'`` 强制浅色
 *
 * 持久化:
 * - 用 ``uni.setStorageSync('xgzh.theme.mode', mode)`` (跨端 storage); 默认 dark
 * - cold start 在 App.vue ``onLaunch`` 调 ``init()``, 立即应用并挂监听
 *
 * 应用方式 (H5 / mp 双轨):
 * - **H5**: ``document.documentElement.setAttribute('data-theme', 'light' | 'dark')``
 *   ``App.vue`` 里 ``:root[data-theme='light']`` 选择器接管 CSS 变量, 即时生效
 * - **mp-weixin**: ``uni.setNavigationBarColor`` 同步 navbar; 内容层 CSS 变量切换
 *   走 ``page.theme-light`` class — 但 mp 不能直接给 ``page`` 加 class (page 是
 *   wxml 根元素). v1 暂只切 navbar, 内容层留待 v2 增强 (用户切到 light 后, mp
 *   会看到 navbar 浅色 + 内容仍深色; 文档注明). H5 端是产品验证主战场, MVP 接受.
 *
 * 设计取舍:
 * - 不在每个组件 ``import { useThemeStore }``, 反复 mount/unmount 浪费; 在 App.vue
 *   ``onLaunch`` init 一次, 后续每个组件 / 页面通过 CSS var 自动响应主题切换
 * - ``effective`` 只算 ``'dark' | 'light'`` (auto 解析后), 业务方不关心 'auto' 这个
 *   中间态; UI 选项 chip / 切换器才需要原始 ``mode``
 */

import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

export type ThemeMode = 'auto' | 'dark' | 'light'
export type EffectiveTheme = 'dark' | 'light'

const STORAGE_KEY = 'xgzh.theme.mode'

/** 系统暗色优先 (H5 走 matchMedia; mp 无该 API 默认 dark) */
function detectSystemTheme(): EffectiveTheme {
  // #ifdef H5
  if (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-color-scheme: light)').matches
  ) {
    return 'light'
  }
  // #endif
  return 'dark'
}

/**
 * 应用主题到 H5 documentElement + mp navbar + tabBar (跨端统一).
 *
 * - H5: ``data-theme`` 属性挂 ``<html>``, CSS 变量自动重算; 不再手动设 navbar.
 * - mp-weixin / app: ``uni.setNavigationBarColor`` 给当前页 navbar 染色
 *   (H5 没有原生 navbar, 通过 ``data-theme`` + CSS 控制).
 * - **tabBar (BUG-S7.2-003)**: ``uni.setTabBarStyle`` H5/mp/app 全端官方支持,
 *   Sprint 7.2 把这个调用从 ``// #ifndef H5`` 移出, 统一调用 — H5 不再依赖
 *   App.vue 的 CSS ``filter: invert`` hack, 视觉与 mp 100% 一致.
 *   (icon 走 PNG path 不能动态换色, 但 PNG 实测 RGB 144/255 中等亮度, 浅深底
 *    都可识别; v2 出第二套浅色 icon 升级.)
 * - page 内容层 CSS 变量切换走 ``view.theme-light`` 选择器 (BUG-S7.1-003);
 *   各页面顶层 view ``:class="[ 'page', themeClass ]"`` 注入即可.
 */
function applyTheme(effective: EffectiveTheme) {
  // #ifdef H5
  if (typeof document !== 'undefined' && document.documentElement) {
    document.documentElement.setAttribute('data-theme', effective)
  }
  // #endif
  // #ifndef H5
  // mp navbar — 与 pages.json 默认色对齐: dark = #0F172A 白字; light = #ffffff 黑字
  try {
    if (effective === 'light') {
      uni.setNavigationBarColor({
        frontColor: '#000000',
        backgroundColor: '#ffffff',
      })
    } else {
      uni.setNavigationBarColor({
        frontColor: '#ffffff',
        backgroundColor: '#0f172a',
      })
    }
  } catch {
    // 部分 mp 端 (调试器 / 早期版本) 不支持; 静默吞
  }
  // #endif

  // BUG-S7.2-003: tabBar 主题同步 (跨端统一). H5/mp/app 都官方支持 setTabBarStyle.
  // borderStyle 仅接 white/black 字面量 (mp 协议限制), 不接任意色值;
  // 浅色主题用 white 给 tabBar 浅色边框.
  try {
    if (effective === 'light') {
      uni.setTabBarStyle({
        color: '#64748b',
        selectedColor: '#2563eb',
        backgroundColor: '#ffffff',
        borderStyle: 'white',
      })
    } else {
      uni.setTabBarStyle({
        color: '#94a3b8',
        selectedColor: '#4f8bff',
        backgroundColor: '#0B1220',
        borderStyle: 'black',
      })
    }
  } catch {
    // 调试器 / 部分 H5 早期版本不支持 setTabBarStyle, 静默吞
  }
}

export const useThemeStore = defineStore('theme', () => {
  const mode = ref<ThemeMode>('dark')
  const systemTheme = ref<EffectiveTheme>(detectSystemTheme())

  /** 实际应用的主题 (auto → 系统; dark/light → 自身) */
  const effective = computed<EffectiveTheme>(() => {
    if (mode.value === 'auto') return systemTheme.value
    return mode.value
  })

  /**
   * BUG-S7.1-003: page 内容层主题 class.
   *
   * mp 端 page 元素不能直接挂 class, 退而求其次给所有页面**顶层 view** 加
   * ``:class="['page', themeClass]"``; 选 ``light`` 时返回 ``'theme-light'``,
   * 配合 App.vue 的 ``view.theme-light { --color-* }`` 选择器, 在该 view 上
   * 重定义 CSS 变量, 子元素全继承到 — 实现内容层主题切换.
   *
   * H5 端这个 class 也会注入但不依赖它生效 (H5 走 ``:root[data-theme]``);
   * 注入它单纯是为了保持 mp / H5 同一套模板代码不分支.
   */
  const themeClass = computed<string>(() =>
    effective.value === 'light' ? 'theme-light' : '',
  )

  /** 冷启动: 读 storage + apply + 监听系统主题变化 */
  function init() {
    try {
      const saved = uni.getStorageSync(STORAGE_KEY) as ThemeMode | ''
      if (saved === 'auto' || saved === 'dark' || saved === 'light') {
        mode.value = saved
      }
    } catch {
      // 不可恢复就走默认 dark
    }
    applyTheme(effective.value)

    // #ifdef H5
    // 监听系统主题变化 (用户切操作系统 dark mode), auto 模式下自动跟随
    if (typeof window !== 'undefined' && typeof window.matchMedia === 'function') {
      const mql = window.matchMedia('(prefers-color-scheme: light)')
      const handler = (e: MediaQueryListEvent) => {
        systemTheme.value = e.matches ? 'light' : 'dark'
        if (mode.value === 'auto') applyTheme(effective.value)
      }
      // addEventListener 兼容性比 addListener 好, 但 Safari 14- 用 addListener
      if (typeof mql.addEventListener === 'function') {
        mql.addEventListener('change', handler)
      } else if (typeof mql.addListener === 'function') {
        mql.addListener(handler)
      }
    }
    // #endif
  }

  /** 切主题: 立即 apply + 持久化 */
  function setMode(next: ThemeMode) {
    if (mode.value === next) return
    mode.value = next
    try {
      uni.setStorageSync(STORAGE_KEY, next)
    } catch {
      // storage 满 / 关闭无非用户体验上"刷新后回到 dark", 不影响功能
    }
    applyTheme(effective.value)
  }

  /**
   * 强制重应用当前主题; 切页时调一次, 让 mp 端每个新页 navbar 都被染色
   * (uni.setNavigationBarColor 是"当前页" scope, 新 push 的页不继承)
   */
  function reapply() {
    applyTheme(effective.value)
  }

  return {
    mode,
    systemTheme,
    effective,
    themeClass,
    init,
    setMode,
    reapply,
  }
})
