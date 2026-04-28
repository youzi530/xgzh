/**
 * 全局 VIP 升级 modal 单例 composable (FE-S2-004 + FE-S3-004 真支付通道接入).
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
 * - **gotoPay vs payWithPlan 双函数**: ``gotoPay`` 给 modal 用 (用户没选套餐, 跳到
 *   /pages/vip/index 选套餐); ``payWithPlan(plan)`` 给 VIP 升级页用 (用户已选套餐,
 *   直接下单 + 拉起支付). 让 modal CTA 不"默默用默认套餐扣 ¥299" 这种暗黑模式
 *
 * - **关弹时不 reset source/quota**: 切页 / 重开沿用上次 context 没问题, 下次 open
 *   会显式覆盖; 只在 close 时把 visible 设 false, 保留 source/quota 让退场动画期
 *   仍然能渲染正确文案 (transition-leave 帧里读 prop 不会突然空白)
 *
 * - **跨端守卫在 composable, 不在每个页面**: ``payWithPlan`` 内部用条件编译切分
 *   MP-WEIXIN 走真实 uni.requestPayment, H5 / App 走 "请在小程序内支付" 占位.
 *   VIP 页面只调 ``payWithPlan(plan)`` 不关心端
 */

import { readonly, ref } from 'vue'

import {
  type CreateOrderResponse,
  type PayablePlan,
  type PaymentParams,
  createWechatOrder,
} from '@/api/payment'
import type { ChatQuotaPayload } from '@/api/chat'
import { useAuthStore } from '@/stores/auth'

/**
 * 当前是否运行在微信小程序端.
 *
 * 用 ``process.env.UNI_PLATFORM`` —— uni-app 编译期 vite 已把这个常量内联,
 * 不同 bundle 里取值不同 (``mp-weixin`` / ``h5`` / ``app-plus``).
 *
 * 不用 ``// #ifdef`` 条件编译宏的原因: 在普通 ``.ts`` 文件 (非 .vue) 里 ts-compile
 * 会先看到所有分支并给 unreachable 警告, 用环境变量更干净一行解决
 */
function isMpWeixin(): boolean {
  return process.env.UNI_PLATFORM === 'mp-weixin'
}

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

/**
 * ``payWithPlan`` 结果, VIP 页 / 调用方据此决定 UI 反馈:
 * - ``ok``       支付成功 (微信回调可能尚未到达, 但 SDK 层已 resolve)
 * - ``cancel``   用户主动取消 (errMsg 'cancel' 等, 不报错)
 * - ``failed``   下单 / 支付失败, ``message`` 给 toast 用
 * - ``unsupported`` 当前端 (H5 / App) 不支持微信小程序支付, ``message`` 给提示用
 */
export type PayResult =
  | { kind: 'ok'; order: CreateOrderResponse }
  | { kind: 'cancel' }
  | { kind: 'failed'; message: string; code?: string }
  | { kind: 'unsupported'; message: string }

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
   * 用户点 modal "立即升级"; 关闭 modal 跳到 /pages/vip/index 让用户选套餐.
   *
   * **不直接拉起支付的原因**:
   * - modal 上没暴露套餐选择, 直接调 createWechatOrder 必须假设默认 plan,
   *   多数用户会因此付了不想付的金额 = 暗黑模式. 跳详情页让用户清楚地选 → 看金额 → 付
   * - VIP 升级页同时承载权益矩阵 / 套餐对比 / 协议入口, 复用一份 UI 而不是 modal 里
   *   塞 4 张套餐卡 (modal 高度 86vh 上限挤不下)
   *
   * 跨端: 全端都跳 /pages/vip/index. H5 / App 端在 vip 页内显示"请在小程序内支付"
   * 占位 (composable.payWithPlan 守卫), 不在跳转层做端判断, 让 UX 一致 ──
   * 用户在 H5 看到 VIP 页知道"卖什么 + 多少钱", 提示"去小程序付", 比直接 alert
   * "本端不支持" 强 (后者会让用户怀疑这功能存在与否)
   */
  function gotoPay() {
    close()
    uni.navigateTo({
      url: '/pages/vip/index',
      fail: (err) => {
        // navigateTo 失败 (例如 vip 页被压到第 11 层 stack — uni 默认 10 层上限)
        // 走 redirectTo 兜底, 用户体验上把 modal 关了等于"取消", 不报错弹窗
        console.warn('[upgradeModal] navigate to /pages/vip/index failed', err)
        uni.redirectTo({
          url: '/pages/vip/index',
          fail: () => {
            // 极端 fallback: reLaunch 也兜不住时 (例如 url 拼错), 静默
          },
        })
      },
    })
  }

  /**
   * 选定套餐后真实下单 + 拉起微信支付 (FE-S3-004).
   *
   * 流程:
   * 1. 跨端守卫: 仅 MP-WEIXIN 端走真实支付 (spec/06 §2.4 "小程序仅微信支付");
   *    H5 / App 返 ``unsupported`` 让 UI 显"请在小程序内支付"
   * 2. ``createWechatOrder({plan})`` → 拿 ``payment_params`` (5 字段微信 JSAPI 协议)
   * 3. ``uni.requestPayment({provider: 'wxpay', ...payment_params})`` 拉起微信收银台
   * 4. 成功 → 回拉 ``auth.refreshMembership()`` 等微信回调到达后端 (回调走 BE-S3-010
   *    内部, 前端拿 active 状态需要 ``GET /vip/me``); 调用方 navigate 到 result 页
   * 5. 失败 / 取消: 不跳页, 由调用方决定 toast / 重试 UI
   *
   * 不在这里 navigate result 页的原因: 让 VIP 页自己控制跳转, 避免 composable 层
   * 直接耦合路由 (例如未来加"支付完成提示 modal" 而不是 result 页)
   *
   * **微信支付到 active 的时序问题**: ``uni.requestPayment.success`` 不代表服务端
   * 回调已经处理. 实际是:
   *   user 在微信收银台输入密码 → 微信扣款 → 微信通知 SDK ``success`` (~100ms) →
   *   微信异步推 ``/pay/wechat/notify`` (~500ms-3s) → BE 处理回调 → vip_memberships.status=active
   *
   * 因此 ``payWithPlan`` resolve 时 BE 可能还没收到回调, ``GET /vip/me`` 仍是
   * 旧状态. 调用方 (result 页) 应轮询 1-3 次 (间隔 1.5s) 再放弃, FE-S3-004
   * result 页里实现这个轮询.
   */
  async function payWithPlan(plan: PayablePlan): Promise<PayResult> {
    // ─── 跨端守卫: 仅 MP-WEIXIN 走真实微信支付 ─────────────────
    // 用条件编译变量 + 运行时判断; uni-app 把 ``process.env.UNI_PLATFORM`` 在编译期注入
    if (!isMpWeixin()) {
      return {
        kind: 'unsupported' as const,
        message: '微信支付仅在小程序内可用, 请扫码进入"新股智汇"小程序',
      }
    }

    // 登录态守卫: 未登录前置短路, 防 401 silent refresh 失败死循环
    const auth = useAuthStore()
    if (!auth.loggedIn) {
      return {
        kind: 'failed' as const,
        message: '请先登录后再进行支付',
        code: 'not_logged_in',
      }
    }

    let order: CreateOrderResponse
    try {
      order = await createWechatOrder({ plan, payment_channel: 'wechat_mp' })
    } catch (e) {
      const err = e as { statusCode?: number; detail?: { detail?: { code?: string; message?: string } } }
      const inner = err?.detail?.detail
      const code = inner?.code ?? `http_${err?.statusCode ?? 0}`
      const message = inner?.message ?? '下单失败, 请稍后重试'
      console.warn('[upgradeModal] createWechatOrder failed', code, message)
      return { kind: 'failed' as const, message, code }
    }

    // 调微信收银台. uni-app 跨端统一 requestPayment API; MP-WEIXIN 内部走
    // wx.requestPayment, 5 字段 (timeStamp/nonceStr/package/signType/paySign) 直透
    const sdkResult = await new Promise<{ ok: boolean; cancelled: boolean; errMsg?: string }>(
      (resolve) => {
        uni.requestPayment({
          provider: 'wxpay',
          ...(order.payment_params as PaymentParams),
          success: () => resolve({ ok: true, cancelled: false }),
          fail: (err) => {
            // wx.requestPayment fail 包含两类: 1) 用户取消 (errMsg ~"requestPayment:fail cancel");
            // 2) 真错 (签名错 / prepay_id 失效 / 网络). 用 errMsg 关键字粗判,
            // 取消和失败的 UX 区别只是"是否 toast"
            const errMsg = (err as { errMsg?: string })?.errMsg ?? ''
            const cancelled = /cancel/i.test(errMsg)
            resolve({ ok: false, cancelled, errMsg })
          },
        })
      },
    )

    if (sdkResult.cancelled) {
      return { kind: 'cancel' as const }
    }
    if (!sdkResult.ok) {
      return {
        kind: 'failed' as const,
        message: '微信支付未完成, 请稍后重试',
        code: 'wxpay_sdk_fail',
      }
    }

    // SDK success: 微信侧已扣款, 后台回调可能还没到. 主动拉一次 membership 让
    // result 页能拿到当前快照 (即便仍是 expired, 轮询逻辑在 result 页继续刷)
    void auth.refreshMembership().catch((e) => {
      console.warn('[upgradeModal] refreshMembership after pay failed', e)
    })

    return { kind: 'ok' as const, order }
  }

  return {
    visible,
    source: readonly(source),
    quota: readonly(quota),
    open,
    close,
    reset,
    gotoPay,
    payWithPlan,
  }
}
