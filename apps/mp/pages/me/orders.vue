<script setup lang="ts">
/**
 * 支付历史页 (FE-S3-005).
 *
 * 路由: ``/pages/me/orders``
 *
 * 模块:
 * 1. 顶部 summary 条: "共 X 笔订单, 累计已支付 ¥Y" (Y 来自 ``vipMembership.total_paid_cny``)
 * 2. 订单列表: plan 名 + 金额 + 创建时间 + 状态徽标 (paid 绿 / pending 黄 / failed/refunded 灰)
 * 3. 下拉刷新 (``onPullDownRefresh``); 不分页 — 后端默认返 20 条 (上限 100), 一次性
 *    全量足够展示 (单用户长期 lifetime + 续费 ≤ 20 笔, 远低于阈值)
 *
 * 设计取舍:
 *
 * - **不分页 / 不触底加载**: BE-S3-009 ``GET /vip/orders`` 设计就是"一次取全, 默认 20",
 *   单用户订单量级低 (lifetime 上限 ≤ 20), 加分页 / 触底逻辑得不偿失. spec 提的"触底加载"
 *   是 P1 优化, 真有用户撞 100 笔上限再说
 *
 * - **状态徽标 vs 文字**: 用色块徽标 (paid 绿 / pending 黄 / failed/refunded 灰) +
 *   极简文字, 比 "已支付 / 待支付 / 已失败" 更视觉一目了然; 复用 spec/03 §统一色板
 *
 * - **summary 条计 ``total_paid_cny``**: 来自 ``vipMembership.total_paid_cny`` (BE
 *   原子算的累计支付额, 财务对账依据), 比前端把列表 sum 加起来更准 (列表只 20 笔,
 *   超过 20 笔的用户算和会少一截)
 *
 * - **空态文案双分支**: 真没订单 (新用户) 显"还没有订阅记录, 升级 VIP 解锁全部权益";
 *   网络错 / 401 显"加载失败, 下拉刷新", 让用户区分"我没买过" vs "暂时拉不到"
 *
 * - **不放"订单详情" 子页**: 列表行已经包含全部信息 (订单号 / 金额 / 时间 / 状态),
 *   再点进详情页只能多 0 信息. 微信支付小程序也是列表展示就完事
 */

import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import { fetchOrders, type OrderItem } from '@/api/vip'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const { vipMembership } = storeToRefs(authStore)

const orders = ref<OrderItem[]>([])
const loading = ref<boolean>(false)
/** 'idle' | 'empty' (网络成功但 0 条) | 'error' (拉失败) */
const phase = ref<'idle' | 'empty' | 'error'>('idle')

const totalPaid = computed(() => {
  const v = vipMembership.value?.total_paid_cny ?? 0
  // BE 返 number 类型 (Decimal 序列化), 直接用; 保留 2 位小数
  return v.toFixed(2)
})

const summaryText = computed(() => {
  const count = orders.value.length
  if (count === 0) return '还没有订阅记录'
  return `共 ${count} 笔订阅, 累计支付 ¥${totalPaid.value}`
})

interface StatusChip {
  label: string
  color: 'green' | 'amber' | 'gray' | 'red'
}

/**
 * order.status (4 态) → 徽标颜色 + 文字.
 *
 * 视觉编码:
 * - paid     → 绿 (积极完成态)
 * - pending  → 琥珀 (中间态, 不报警但提示中)
 * - failed   → 灰 (终止态, 不强红色 — 微信支付失败多是"用户取消", 不需要"事故感")
 * - refunded → 红 (例外态, 罕见但要醒目)
 */
const STATUS_CHIPS: Record<string, StatusChip> = {
  paid: { label: '已支付', color: 'green' },
  pending: { label: '待支付', color: 'amber' },
  failed: { label: '已失败', color: 'gray' },
  refunded: { label: '已退款', color: 'red' },
}

function getChip(status: string): StatusChip {
  return STATUS_CHIPS[status] || { label: status, color: 'gray' }
}

const PLAN_LABELS: Record<string, string> = {
  monthly: '月度',
  quarterly: '季度',
  yearly: '年度',
  lifetime: '终身',
  trial: '试用',
}

function planLabel(p: string): string {
  return PLAN_LABELS[p] || p
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${Y}-${M}-${D} ${h}:${m}`
}

async function loadOrders() {
  loading.value = true
  try {
    const resp = await fetchOrders(100)
    orders.value = resp.items
    phase.value = resp.items.length === 0 ? 'empty' : 'idle'
    // 顺手刷一次会员状态 (total_paid_cny 同步 + 让 me 页回来时拿到最新)
    void authStore.refreshMembership()
  } catch (e) {
    console.warn('[orders] fetchOrders failed', e)
    phase.value = 'error'
  } finally {
    loading.value = false
  }
}

function gotoVip() {
  uni.navigateTo({ url: '/pages/vip/index' })
}

onLoad(() => {
  if (!authStore.loggedIn) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  void loadOrders()
})

onPullDownRefresh(async () => {
  await loadOrders()
  uni.stopPullDownRefresh()
})
</script>

<template>
  <view class="page">
    <!-- ─── summary 条 ─── -->
    <view class="summary">
      <text class="summary-text">{{ summaryText }}</text>
      <text v-if="vipMembership?.has_active" class="summary-badge">VIP 已激活</text>
    </view>

    <!-- ─── 列表 / 空态 / 错误 ─── -->
    <view v-if="loading && orders.length === 0" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="phase === 'error'" class="state-block">
      <text class="state-emoji">😕</text>
      <text class="state-text">加载失败</text>
      <text class="state-sub">网络异常或登录过期, 下拉刷新重试</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state-block">
      <text class="state-emoji">🌱</text>
      <text class="state-text">还没有订阅记录</text>
      <text class="state-sub">升级 VIP 解锁全部 AI 深度功能</text>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="gotoVip">
        <text class="state-cta-text">开通 VIP</text>
      </view>
    </view>

    <view v-else class="orders">
      <view v-for="o in orders" :key="o.order_id" class="order-row">
        <view class="order-head">
          <text class="order-plan">{{ planLabel(o.plan) }}</text>
          <view :class="['order-chip', `order-chip-${getChip(o.status).color}`]">
            <text class="order-chip-text">{{ getChip(o.status).label }}</text>
          </view>
        </view>
        <view class="order-body">
          <text class="order-amount">¥ {{ o.amount_cny }}</text>
          <text class="order-time">{{ formatDate(o.created_at) }}</text>
        </view>
        <view class="order-foot">
          <text class="order-no">订单号: {{ o.out_trade_no }}</text>
          <text v-if="o.paid_at" class="order-paid-at">支付于 {{ formatDate(o.paid_at) }}</text>
        </view>
      </view>

      <text class="orders-tail">— 仅展示最近 {{ orders.length }} 笔订单 —</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

/* ─── summary ─── */
.summary {
  padding: 24rpx 28rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16rpx;
}
.summary-text {
  flex: 1;
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
  font-weight: 600;
}
.summary-badge {
  flex-shrink: 0;
  padding: 6rpx 16rpx;
  background: linear-gradient(135deg, #f6c453, #d97706);
  color: #1a1305;
  font-size: 20rpx;
  font-weight: 700;
  border-radius: 999rpx;
}

/* ─── state blocks ─── */
.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
  padding: 80rpx 32rpx;
}
.state-emoji {
  font-size: 80rpx;
  line-height: 1;
}
.state-text {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.state-sub {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}
.state-cta {
  margin-top: 24rpx;
  padding: 22rpx 64rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #f6c453, #d97706);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.32);
}
.state-cta-hover {
  background: linear-gradient(135deg, #d97706, #b45309);
}
.state-cta-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #1a1305;
}

/* ─── order row ─── */
.orders {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.order-row {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.order-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
}

.order-plan {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.order-chip {
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  border: 1rpx solid;
}
.order-chip-text {
  font-size: 20rpx;
  font-weight: 700;
}
.order-chip-green {
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.4);
}
.order-chip-green .order-chip-text {
  color: #22c55e;
}
.order-chip-amber {
  background: rgba(246, 196, 83, 0.12);
  border-color: rgba(246, 196, 83, 0.4);
}
.order-chip-amber .order-chip-text {
  color: #f6c453;
}
.order-chip-gray {
  background: rgba(148, 163, 184, 0.12);
  border-color: rgba(148, 163, 184, 0.32);
}
.order-chip-gray .order-chip-text {
  color: #94a3b8;
}
.order-chip-red {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.4);
}
.order-chip-red .order-chip-text {
  color: #ef4444;
}

.order-body {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  justify-content: space-between;
  gap: 16rpx;
}
.order-amount {
  font-size: 36rpx;
  font-weight: 800;
  color: #f6c453;
}
.order-time {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.order-foot {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.04);
  padding-top: 12rpx;
  margin-top: 4rpx;
}
.order-no {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  /* 订单号长度 32+ 字符, 单行展示 + 省略 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.order-paid-at {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.8;
}

.orders-tail {
  margin-top: 12rpx;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.6;
}
</style>
