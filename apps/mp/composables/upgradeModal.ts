/**
 * 全局 VIP 升级 modal 单例 composable (FE-S2-004).
 *
 * 用法
 * ====
 *
 * 在任意 ``<script setup>`` 里调:
 *
 * ```ts
 * const upgrade = useUpgradeModal()
 * upgrade.open({ source: 'quota_banner', quota: globalError.value?.quota })
 * ```
 *
 * 在页面模板末尾挂一个 ``<UpgradeVipModal />``, 它会自动从本 composable 读单例 state,
 * 不需要 prop drilling. 不同页面打开同一个 modal 时, 会复用同一份 visible / quota /
 * source state, 切页时 onUnload 不必 reset (单例落到模块级 ref, 跨页保留).
 *
 * 设计取舍
 * ========
 *
 * - **单例 vs 每页 ref**: 升级 modal 在 agent / me / detail / 首页等多入口都会用,
 *   单例避免每个入口各自维护 visible 状态; 让"哪里点的"都收敛到 ``source`` 字段
 *   方便做 GA 上报 (Sprint 3 加埋点时直接读 source)
 *
 * - **不上 Pinia**: 状态简单 (3 个 ref) 且不与持久化 / 业务领域耦合, 模块级 ref
 *   足够; Pinia 适合"会被 SSR 关心"或"跨多个 store 联动"的复杂状态. 这里都不沾边
 *
 * - **gotoPay 兜底**: 当前没有支付通道, 走 ``uni.showModal`` 占位; Sprint 3 引入
 *   微信支付 / Apple IAP 后, 在这里替换为 ``uni.requestPayment`` 即可, 调用方
 *   不用改. 这就是"组件接事件, 跳支付逻辑放 composable"的好处
 *
 * - **关弹时不 reset source/quota**: 切页 / 重开沿用上次 context 没问题, 下次 open
 *   会显式覆盖; 只在 close 时把 visible 设 false, 保留 source/quota 让退场动画期
 *   仍然能渲染正确文案 (transition-leave 帧里读 prop 不会突然空白)
 */

import { readonly, ref } from 'vue'

import type { ChatQuotaPayload } from '@/api/chat'

/** 触发升级 modal 的入口来源, 用于 GA + 文案微调 (例如 quota_banner 时强调"今日额度") */
export type UpgradeSource =
  | 'quota_banner' // agent 页全局 quota banner CTA
  | 'inline_error' // assistant 气泡内嵌 quota 错误 CTA
  | 'me_page' // 个人中心 VIP 卡片
  | 'manual' // 默认 / 手动触发, 测试 / 其他入口

interface OpenPayload {
  source: UpgradeSource
  quota?: ChatQuotaPayload | null
}

// ─── 模块级单例 state ─────────────────────────────────────────────
const visible = ref(false)
const source = ref<UpgradeSource>('manual')
const quota = ref<ChatQuotaPayload | null>(null)

export function useUpgradeModal() {
  function open(payload: OpenPayload) {
    source.value = payload.source
    quota.value = payload.quota ?? null
    visible.value = true
  }

  function close() {
    visible.value = false
  }

  /**
   * 完全 reset 单例状态: visible / source / quota 全清.
   *
   * 与 ``close`` 区别:
   * - ``close``  仅 visible=false, 保留 source / quota — 让退场动画期间 UI 不闪
   *               (例如 banner 来源关弹后, source='quota_banner' 留着等下次 open 复用)
   * - ``reset``  source 回 'manual', quota 回 null — 用于"语义边界变化"场景:
   *               1) 用户登录 / 登出 (auth setSession / clearSession): 旧 quota 已经
   *                  跟新身份无关, 留着会让下次 open 显错套餐
   *               2) Pinia store 主动 reset: chat store reset 时配额上下文也作废
   *
   * 加这个的真实原因: visible 是模块级 ref, 跨页面跨 setup 都共享一份. 在 agent
   * 页 quota 触发 open 后没成功 close (例如旧 catchtap noop bug 把按钮事件吃掉),
   * 用户切到 me 页, ``<UpgradeVipModal />`` 挂载时读到 visible=true → 立即显示,
   * 用户体验是"我刚进 me 页就弹了升级 VIP". 即便修了 modal 关闭按钮, 也应该在
   * "登录态变化"这种语义边界把状态清干净, 不依赖用户必须点 X.
   */
  function reset() {
    visible.value = false
    source.value = 'manual'
    quota.value = null
  }

  /**
   * 用户点"立即升级"; 当前支付通道未上线, 走占位 modal.
   *
   * Sprint 3 落实支付时, 这里改成:
   * - MP-WEIXIN: ``uni.requestPayment({ provider: 'wxpay', ... })``
   * - App: 同上 (uni-app 跨端 API)
   * - H5: 跳 PC 收银台或微信 H5 支付
   */
  function gotoPay() {
    close()
    uni.showModal({
      title: '支付通道开发中',
      content:
        '我们正在与微信支付 / Apple IAP 对接中, 上线后第一时间通知。\n\n现阶段所有 AI 功能限时免费开放, 感谢支持！',
      showCancel: false,
      confirmText: '我知道了',
    })
  }

  return {
    visible,
    source: readonly(source),
    quota: readonly(quota),
    open,
    close,
    reset,
    gotoPay,
  }
}
