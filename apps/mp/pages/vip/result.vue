<script setup lang="ts">
/**
 * VIP 支付结果页 (FE-S3-004).
 *
 * 路由: ``/pages/vip/result?status=success&order_id=XXX&plan=monthly``
 *
 * 状态:
 * - ``status=success``       支付成功; 主动轮询 ``GET /vip/me`` 等微信回调到达
 *   (3 次, 间隔 1.5s); 拿到 ``has_active=true`` → 显示 ✅ + 跳"我的"
 * - ``status=failed``        支付失败; 显示 ❌ + 重试按钮
 * - ``status=cancel``        用户取消; 友好文案, 跳回升级页
 * - ``status=unsupported``   当前端不支持; 提示去小程序
 *
 * 轮询逻辑:
 *
 * uni.requestPayment.success 时, 微信侧已扣款但服务端回调可能尚未送达
 * (异步推送, 100ms - 3s 不等). 直接拉 ``GET /vip/me`` 可能仍是旧状态;
 * 因此进 result 页后:
 *   t=0       立刻拉一次 (大概率仍 expired/null)
 *   t=1.5s    再拉一次 (60% 概率回调已到)
 *   t=3.0s    再拉一次 (90% 概率)
 *   t=4.5s    再拉一次 (99% 概率); 仍 expired → 视为"回调延迟", 显示"已扣款,
 *             VIP 状态稍后将自动激活, 如长时间未生效请联系客服"
 *
 * 为什么不开 SSE / Websocket 推: 资源 vs 收益不划算 — 单次支付 → 客户端只在这
 * 一页关心 active 状态 → 短轮询就够; SSE 全局连接维护成本远高于这点延迟收益.
 *
 * 关于"显示已扣款但 VIP 仍 expired" 的 UX: 给用户清晰预期 + 联系方式, 不让用户
 * 干等; 99% 场景 4.5s 内已 active, 真正延迟到这步的极少, 但要兜住.
 */

import { onLoad, onUnload } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import { fetchOrders, type OrderItem } from '@/api/vip'
import { useAuthStore } from '@/stores/auth'

type Status = 'success' | 'failed' | 'cancel' | 'unsupported'

const auth = useAuthStore()

const status = ref<Status>('success')
const orderId = ref<string | null>(null)
const plan = ref<string | null>(null)

const polling = ref(false)
const pollAttempts = ref(0)
const POLL_TIMES = 3
const POLL_INTERVAL_MS = 1500
let pollTimer: number | null = null

const order = ref<OrderItem | null>(null)

const isVipActive = computed(() => auth.vipMembership?.has_active ?? false)
const isPollingTimedOut = computed(
  () => status.value === 'success' && pollAttempts.value >= POLL_TIMES && !isVipActive.value,
)

const heroEmoji = computed(() => {
  if (status.value === 'success' && isVipActive.value) return '🎉'
  if (status.value === 'success') return '⏳' // 已扣款, 等回调
  if (status.value === 'cancel') return '🤝'
  if (status.value === 'unsupported') return '📱'
  return '😕'
})

const heroTitle = computed(() => {
  if (status.value === 'success' && isVipActive.value) return '支付成功, VIP 已激活'
  if (status.value === 'success' && isPollingTimedOut.value) return '已收到支付, 正在激活'
  if (status.value === 'success') return '支付成功, 正在激活会员…'
  if (status.value === 'cancel') return '已取消支付'
  if (status.value === 'unsupported') return '请在小程序内支付'
  return '支付未完成'
})

const heroSubtitle = computed(() => {
  if (status.value === 'success' && isVipActive.value) {
    const m = auth.vipMembership
    if (m?.plan === 'lifetime') return '终身 VIP 已开通, 解锁全部权益'
    if (m?.end_at) return `VIP 有效期至 ${formatDate(m.end_at)}`
    return '会员状态已激活, 享受全部 VIP 权益'
  }
  if (status.value === 'success' && isPollingTimedOut.value) {
    return '微信支付已扣款, VIP 状态将在 1 - 2 分钟内自动激活. 如长时间未生效请联系客服反馈.'
  }
  if (status.value === 'success') return '微信支付已完成, 正在等待会员状态同步…'
  if (status.value === 'cancel') return '已取消本次支付, 你可以随时回到 VIP 页面重试.'
  if (status.value === 'unsupported') return '微信支付仅在小程序内可用, 请扫码进入"新股智汇"小程序.'
  return '本次支付未完成, 你可以重新发起.'
})

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const PLAN_LABELS: Record<string, string> = {
  monthly: '月度',
  quarterly: '季度',
  yearly: '年度',
  lifetime: '终身',
  trial: '试用',
}

function planLabel(p: string | null | undefined): string {
  if (!p) return '-'
  return PLAN_LABELS[p] || p
}

async function pollMembership() {
  if (!polling.value) return
  pollAttempts.value += 1
  await auth.refreshMembership()
  // 已激活: 顺手把订单详情拉出来展示 (订单号 / 金额 / 时间), 失败不打断
  if (isVipActive.value && orderId.value) {
    try {
      const list = await fetchOrders(20)
      const matched = list.items.find((o) => o.order_id === orderId.value)
      if (matched) order.value = matched
    } catch (e) {
      console.warn('[vip-result] fetchOrders failed', e)
    }
  }
  if (isVipActive.value) {
    polling.value = false
    return
  }
  if (pollAttempts.value < POLL_TIMES) {
    pollTimer = setTimeout(pollMembership, POLL_INTERVAL_MS) as unknown as number
  } else {
    polling.value = false
  }
}

function startPolling() {
  if (polling.value) return
  polling.value = true
  pollAttempts.value = 0
  void pollMembership()
}

function gotoMe() {
  // FE-S6-001: me 是 tab 页, switchTab 切换保留其它 tab state; 失败兜底 reLaunch
  uni.switchTab({
    url: '/pages/me/index',
    fail: () => uni.reLaunch({ url: '/pages/me/index' }),
  })
}

function gotoHome() {
  // FE-S6-001: 同上
  uni.switchTab({
    url: '/pages/index/index',
    fail: () => uni.reLaunch({ url: '/pages/index/index' }),
  })
}

function gotoVip() {
  uni.redirectTo({ url: '/pages/vip/index' })
}

onLoad((options) => {
  const opts = (options ?? {}) as Partial<Record<'status' | 'order_id' | 'plan', string>>
  const s = opts.status
  if (s === 'success' || s === 'failed' || s === 'cancel' || s === 'unsupported') {
    status.value = s
  } else {
    status.value = 'failed'
  }
  orderId.value = opts.order_id ?? null
  plan.value = opts.plan ?? null

  if (status.value === 'success') {
    startPolling()
  }
})

onUnload(() => {
  // 清轮询定时器, 防用户离开后还在偷偷 setTimeout 触发 setData
  if (pollTimer !== null) {
    clearTimeout(pollTimer)
    pollTimer = null
  }
  polling.value = false
})
</script>

<template>
  <view class="page">
    <view class="hero">
      <text class="hero-emoji">{{ heroEmoji }}</text>
      <text class="hero-title">{{ heroTitle }}</text>
      <text class="hero-subtitle">{{ heroSubtitle }}</text>

      <view v-if="status === 'success' && polling && !isVipActive" class="loading-row">
        <text class="loading-dot loading-dot-1">·</text>
        <text class="loading-dot loading-dot-2">·</text>
        <text class="loading-dot loading-dot-3">·</text>
      </view>
    </view>

    <view v-if="status === 'success' && order" class="order-card">
      <view class="order-row">
        <text class="order-label">订单号</text>
        <text class="order-value">{{ order.out_trade_no }}</text>
      </view>
      <view class="order-row">
        <text class="order-label">套餐</text>
        <text class="order-value">{{ planLabel(order.plan) }}</text>
      </view>
      <view class="order-row">
        <text class="order-label">支付金额</text>
        <text class="order-value">¥ {{ order.amount_cny }}</text>
      </view>
      <view v-if="order.paid_at" class="order-row">
        <text class="order-label">支付时间</text>
        <text class="order-value">{{ formatDate(order.paid_at) }}</text>
      </view>
    </view>

    <view class="actions">
      <!-- success + active: 单 CTA "去我的" -->
      <view
        v-if="status === 'success' && isVipActive"
        class="action-btn action-btn-primary"
        hover-class="action-btn-primary-hover"
        @tap="gotoMe"
      >
        <text class="action-btn-text">去我的页面</text>
      </view>

      <!-- success + 仍轮询: 等待 + 兜底"先回首页" -->
      <view
        v-else-if="status === 'success' && polling"
        class="action-btn action-btn-secondary"
        hover-class="action-btn-secondary-hover"
        @tap="gotoHome"
      >
        <text class="action-btn-text-ghost">先回首页, 稍后会自动激活</text>
      </view>

      <!-- success + 轮询超时: 兜底文案 -->
      <view v-else-if="status === 'success'" class="actions-row">
        <view
          class="action-btn action-btn-secondary"
          hover-class="action-btn-secondary-hover"
          @tap="gotoHome"
        >
          <text class="action-btn-text-ghost">回首页</text>
        </view>
        <view
          class="action-btn action-btn-primary"
          hover-class="action-btn-primary-hover"
          @tap="gotoMe"
        >
          <text class="action-btn-text">去我的</text>
        </view>
      </view>

      <!-- failed / cancel: 重试 -->
      <view v-else-if="status === 'failed' || status === 'cancel'" class="actions-row">
        <view
          class="action-btn action-btn-secondary"
          hover-class="action-btn-secondary-hover"
          @tap="gotoHome"
        >
          <text class="action-btn-text-ghost">回首页</text>
        </view>
        <view
          class="action-btn action-btn-primary"
          hover-class="action-btn-primary-hover"
          @tap="gotoVip"
        >
          <text class="action-btn-text">重新选择套餐</text>
        </view>
      </view>

      <!-- unsupported -->
      <view
        v-else-if="status === 'unsupported'"
        class="action-btn action-btn-secondary"
        hover-class="action-btn-secondary-hover"
        @tap="gotoHome"
      >
        <text class="action-btn-text-ghost">回首页</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 64rpx 32rpx 64rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 32rpx;
}

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16rpx;
  padding: 48rpx 24rpx;
  background: linear-gradient(180deg, rgba(246, 196, 83, 0.12), rgba(246, 196, 83, 0));
  border: 1rpx solid rgba(246, 196, 83, 0.2);
  border-radius: 24rpx;
}

.hero-emoji {
  font-size: 96rpx;
  line-height: 1;
}

.hero-title {
  font-size: 36rpx;
  font-weight: 700;
  color: #f6c453;
  text-align: center;
}

.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  opacity: 0.85;
  text-align: center;
  line-height: 1.6;
  padding: 0 24rpx;
}

.loading-row {
  display: flex;
  flex-direction: row;
  gap: 8rpx;
  margin-top: 16rpx;
}

.loading-dot {
  font-size: 36rpx;
  font-weight: 700;
  color: #f6c453;
  animation: dot-pulse 1.2s infinite;
}

.loading-dot-1 {
  animation-delay: 0s;
}
.loading-dot-2 {
  animation-delay: 0.2s;
}
.loading-dot-3 {
  animation-delay: 0.4s;
}

@keyframes dot-pulse {
  0%,
  60%,
  100% {
    opacity: 0.2;
    transform: translateY(0);
  }
  30% {
    opacity: 1;
    transform: translateY(-6rpx);
  }
}

.order-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.order-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}

.order-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.order-value {
  font-size: 26rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.actions {
  margin-top: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.actions-row {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
}

.action-btn {
  flex: 1;
  padding: 24rpx 0;
  text-align: center;
  border-radius: 999rpx;
}

.action-btn-primary {
  background: linear-gradient(135deg, #f6c453, #d97706);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.32);
}

.action-btn-primary-hover {
  background: linear-gradient(135deg, #d97706, #b45309);
}

.action-btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

.action-btn-secondary-hover {
  background: rgba(255, 255, 255, 0.16);
}

.action-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #1a1305;
}

.action-btn-text-ghost {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
</style>
