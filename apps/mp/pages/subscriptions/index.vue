<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 中签 tab 主页 (FE-S6-002 接 BE-S6-001/002/003).
 *
 * 路由: /pages/subscriptions/index  (tabBar 第 2 槽位)
 *
 * 视图层次:
 * - hero: 页面标题 + "+ 录入" 按钮
 * - 账户切换器: chip 横滑 (全部 + 各账户 + "+"创建)
 * - 月度汇总卡片: 当月 / 当年 中签数 / 实现盈亏 / 浮盈
 * - 中签列表: 按 listed_at desc, NULL 末尾
 * - 空状态: 没账户 → 引导创建; 没 records → 引导录入
 *
 * 设计要点:
 * - **未登录直接跳登录页**: 中签是私有数据, 不允许匿名访问
 * - **暗色 token 沿用**: 与 me/index.vue 一致
 * - **金额一律字符串**: 后端返 Decimal as string, FE 不做 Number 解析以保精度;
 *   只有展示时调 ``formatPnL`` 加 +/-/￥/亿/万 修饰
 * - **listed_at NULL = 未上市**: 标 "待上市" 灰色 chip
 * - **路径占位**: "+录入" / 编辑 record 跳 ``/pages/subscriptions/edit`` (FE-S6-003 实现)
 *   暂时 toast 未上线即可, 不阻塞主页 demo
 */

import { onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  type SubscriptionAccount,
  type SubscriptionRecord,
  type SubscriptionSummaryGroup,
  getSummary,
  listAccounts,
  listRecords,
  parseSubscriptionError,
} from '@/api/subscription'
import { readAccessTokenSync } from '@/stores/auth'

function isLoggedIn(): boolean {
  return readAccessTokenSync() !== null
}

const accounts = ref<SubscriptionAccount[]>([])
const records = ref<SubscriptionRecord[]>([])
const totalSummary = ref<SubscriptionSummaryGroup | null>(null)
const monthGroups = ref<SubscriptionSummaryGroup[]>([])
const selectedAccountId = ref<string | 'all'>('all')
const loading = ref(false)
const errorMsg = ref('')

const currentMonthSummary = computed<SubscriptionSummaryGroup | null>(() => {
  if (monthGroups.value.length === 0) return null
  // 后端按 key desc 排, 第一项就是最新月; 如果第一项不是当月可能用户本月没操作
  const now = new Date()
  const yyyy = now.getFullYear()
  const mm = String(now.getMonth() + 1).padStart(2, '0')
  const currentKey = `${yyyy}-${mm}`
  return monthGroups.value.find((g) => g.key === currentKey) ?? null
})

const filteredRecords = computed(() => {
  if (selectedAccountId.value === 'all') return records.value
  return records.value.filter((r) => r.account_id === selectedAccountId.value)
})

const hasAccounts = computed(() => accounts.value.length > 0)
const hasRecords = computed(() => filteredRecords.value.length > 0)

async function refresh() {
  if (!isLoggedIn()) {
    uni.redirectTo({ url: '/pages/auth/login' })
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const [accs, recs, sum] = await Promise.all([
      listAccounts(),
      listRecords({ limit: 50 }),
      getSummary({ group_by: 'month' }),
    ])
    accounts.value = accs.items
    records.value = recs.items
    monthGroups.value = sum.groups
    totalSummary.value = sum.total
  } catch (err) {
    const e = parseSubscriptionError(err)
    if (e.code === 'unauthorized') {
      uni.redirectTo({ url: '/pages/auth/login' })
      return
    }
    errorMsg.value = e.message
    uni.showToast({ title: e.message, icon: 'none' })
  } finally {
    loading.value = false
  }
}

onShow(() => {
  void refresh()
})

function formatPnL(s: string | null): string {
  if (s === null || s === undefined) return '—'
  const n = Number(s)
  if (Number.isNaN(n)) return s
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}`
}

function pnlClass(s: string | null): string {
  if (s === null || s === undefined) return 'pnl-neutral'
  const n = Number(s)
  if (Number.isNaN(n)) return 'pnl-neutral'
  if (n > 0) return 'pnl-up'
  if (n < 0) return 'pnl-down'
  return 'pnl-neutral'
}

function selectAccount(id: string | 'all') {
  selectedAccountId.value = id
}

function gotoEdit(recordId?: string) {
  // FE-S6-003 待实现; 当前跳一个 placeholder 路径或 toast
  const url = recordId
    ? `/pages/subscriptions/edit?id=${recordId}`
    : '/pages/subscriptions/edit'
  uni.navigateTo({
    url,
    fail: () => {
      uni.showToast({ title: '录入页即将上线', icon: 'none' })
    },
  })
}

function gotoAccountManage() {
  uni.navigateTo({
    url: '/pages/subscriptions/accounts',
    fail: () => {
      uni.showToast({ title: '账户管理即将上线', icon: 'none' })
    },
  })
}

function getAccountLabel(accountId: string): string {
  const acc = accounts.value.find((a) => a.id === accountId)
  return acc?.label ?? '未知账户'
}
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- Hero -->
    <view class="hero">
      <view class="hero-text">
        <text class="hero-title">中签记账</text>
        <text class="hero-subtitle">月 / 年 / 单股 P&L 一目了然</text>
      </view>
      <view class="hero-add" hover-class="hero-add-hover" :hover-stay-time="80" @tap="() => gotoEdit()">
        <text class="hero-add-emoji">+</text>
      </view>
    </view>

    <!-- 账户切换器 -->
    <scroll-view class="acc-scroll" scroll-x show-scrollbar="false">
      <view class="acc-row">
        <view
          class="acc-chip"
          :class="{ 'acc-chip-active': selectedAccountId === 'all' }"
          @tap="selectAccount('all')"
        >
          <text class="acc-chip-text">全部账户</text>
        </view>
        <view
          v-for="acc in accounts"
          :key="acc.id"
          class="acc-chip"
          :class="{ 'acc-chip-active': selectedAccountId === acc.id }"
          @tap="selectAccount(acc.id)"
        >
          <text v-if="acc.is_primary" class="acc-chip-badge">主</text>
          <text class="acc-chip-text">{{ acc.label }}</text>
        </view>
        <view class="acc-chip acc-chip-add" @tap="gotoAccountManage">
          <text class="acc-chip-text">+ 管理账户</text>
        </view>
      </view>
    </scroll-view>

    <!-- 汇总卡片 -->
    <view v-if="hasAccounts || hasRecords" class="summary">
      <view class="summary-card">
        <text class="summary-label">本月</text>
        <text class="summary-count">
          中签 <text class="summary-num">{{ currentMonthSummary?.allotted_count ?? 0 }}</text>
        </text>
        <text class="summary-pnl" :class="pnlClass(currentMonthSummary?.realized_pnl ?? null)">
          {{ formatPnL(currentMonthSummary?.realized_pnl ?? null) }}
        </text>
      </view>
      <view class="summary-card">
        <text class="summary-label">累计</text>
        <text class="summary-count">
          中签 <text class="summary-num">{{ totalSummary?.allotted_count ?? 0 }}</text>
        </text>
        <text class="summary-pnl" :class="pnlClass(totalSummary?.realized_pnl ?? null)">
          {{ formatPnL(totalSummary?.realized_pnl ?? null) }}
        </text>
      </view>
      <view class="summary-card">
        <text class="summary-label">浮盈</text>
        <text class="summary-count">{{ totalSummary?.count ?? 0 }} 笔</text>
        <text class="summary-pnl" :class="pnlClass(totalSummary?.unrealized_pnl ?? null)">
          {{ formatPnL(totalSummary?.unrealized_pnl ?? null) }}
        </text>
      </view>
    </view>

    <!-- 列表 -->
    <view class="list">
      <text class="list-title">明细</text>

      <!-- 空账户引导 -->
      <view v-if="!hasAccounts && !loading" class="empty">
        <text class="empty-emoji">📒</text>
        <text class="empty-title">还没有任何账户</text>
        <text class="empty-desc">先添加你常用的券商账户(招商 / 华盛 / 富途...)</text>
        <view class="empty-btn" hover-class="empty-btn-hover" :hover-stay-time="80" @tap="gotoAccountManage">
          <text class="empty-btn-text">+ 创建账户</text>
        </view>
      </view>

      <!-- 空 records 引导 -->
      <view v-else-if="!hasRecords && !loading" class="empty">
        <text class="empty-emoji">🎯</text>
        <text class="empty-title">还没有录入中签</text>
        <text class="empty-desc">在券商 APP 看到中签后, 来这里记一笔</text>
        <view class="empty-btn" hover-class="empty-btn-hover" :hover-stay-time="80" @tap="() => gotoEdit()">
          <text class="empty-btn-text">+ 录入第一条</text>
        </view>
      </view>

      <!-- 列表项 -->
      <view
        v-for="r in filteredRecords"
        v-else
        :key="r.id"
        class="record"
        hover-class="record-hover"
        :hover-stay-time="80"
        @tap="() => gotoEdit(r.id)"
      >
        <view class="record-head">
          <view class="record-meta">
            <text class="record-code">{{ r.ipo_code }}</text>
            <text v-if="r.ipo_name" class="record-name">{{ r.ipo_name }}</text>
            <text class="record-account">{{ getAccountLabel(r.account_id) }}</text>
          </view>
          <text v-if="r.allotted_shares > 0" class="record-tag tag-win">
            中 {{ r.allotted_shares }}
          </text>
          <text v-else class="record-tag tag-miss">未中</text>
        </view>
        <view class="record-body">
          <text class="record-date">
            {{ r.subscribed_at }}
            <text v-if="r.listed_at"> → {{ r.listed_at }}</text>
            <text v-else class="record-pending">(待上市)</text>
          </text>
          <view class="record-pnl-row">
            <text v-if="r.realized_pnl !== null" class="record-pnl" :class="pnlClass(r.realized_pnl)">
              已实现 {{ formatPnL(r.realized_pnl) }}
            </text>
            <text v-if="r.unrealized_pnl !== null" class="record-pnl" :class="pnlClass(r.unrealized_pnl)">
              浮盈 {{ formatPnL(r.unrealized_pnl) }}
            </text>
          </view>
        </view>
      </view>
    </view>

    <!-- loading toast (顶部) -->
    <view v-if="loading" class="loading-bar">
      <text class="loading-text">加载中...</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 32rpx 32rpx 80rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

// ─── Hero ──────────────────────────────────────────────────
.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8rpx 4rpx;
}
.hero-text {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.hero-title {
  font-size: 40rpx;
  font-weight: 700;
}
.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.hero-add {
  width: 72rpx;
  height: 72rpx;
  border-radius: 36rpx;
  background: var(--color-accent, #4f8bff);
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 4rpx 20rpx rgba(79, 139, 255, 0.4);
}
.hero-add-hover {
  background: #3a72e0;
}
.hero-add-emoji {
  color: #fff;
  font-size: 44rpx;
  line-height: 1;
  font-weight: 600;
}

// ─── 账户切换器 ────────────────────────────────────────────
.acc-scroll {
  width: 100%;
  white-space: nowrap;
}
.acc-row {
  display: inline-flex;
  gap: 16rpx;
  padding: 4rpx 0;
}
.acc-chip {
  display: inline-flex;
  align-items: center;
  gap: 8rpx;
  padding: 14rpx 28rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.acc-chip-active {
  background: rgba(79, 139, 255, 0.15);
  border-color: rgba(79, 139, 255, 0.5);
}
.acc-chip-add {
  background: transparent;
  border-style: dashed;
}
.acc-chip-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.acc-chip-badge {
  font-size: 20rpx;
  padding: 2rpx 10rpx;
  background: rgba(246, 196, 83, 0.2);
  color: #f6c453;
  border-radius: 8rpx;
}

// ─── 汇总卡 ────────────────────────────────────────────────
.summary {
  display: flex;
  gap: 16rpx;
  margin-top: 8rpx;
}
.summary-card {
  flex: 1;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx 16rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.summary-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.summary-count {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.summary-num {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.summary-pnl {
  font-size: 32rpx;
  font-weight: 700;
}
.pnl-up {
  color: #f87171;
}
.pnl-down {
  color: #34d399;
}
.pnl-neutral {
  color: var(--color-text-muted, #94a3b8);
}

// ─── 列表 ──────────────────────────────────────────────────
.list {
  margin-top: 16rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.list-title {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  padding: 8rpx 4rpx;
}
.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16rpx;
  padding: 80rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  border: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.1));
}
.empty-emoji {
  font-size: 80rpx;
}
.empty-title {
  font-size: 30rpx;
  font-weight: 600;
}
.empty-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.6;
}
.empty-btn {
  margin-top: 8rpx;
  padding: 18rpx 48rpx;
  background: var(--color-accent, #4f8bff);
  color: #fff;
  border-radius: 999rpx;
}
.empty-btn-hover {
  background: #3a72e0;
}
.empty-btn-text {
  font-size: 28rpx;
  color: #fff;
  font-weight: 600;
}

.record {
  background: var(--color-surface, #131a2c);
  border-radius: 20rpx;
  padding: 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.record-hover {
  background: rgba(255, 255, 255, 0.04);
}
.record-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16rpx;
}
.record-meta {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  flex: 1;
  min-width: 0;
}
.record-code {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.record-name {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.record-account {
  font-size: 22rpx;
  color: var(--color-text-dim, #64748b);
}
.record-tag {
  font-size: 22rpx;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  font-weight: 600;
  flex-shrink: 0;
}
.tag-win {
  background: rgba(248, 113, 113, 0.15);
  color: #f87171;
}
.tag-miss {
  background: rgba(148, 163, 184, 0.15);
  color: #94a3b8;
}
.record-body {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16rpx;
  flex-wrap: wrap;
}
.record-date {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.record-pending {
  font-style: italic;
}
.record-pnl-row {
  display: flex;
  gap: 16rpx;
}
.record-pnl {
  font-size: 24rpx;
  font-weight: 600;
}

// ─── loading bar ─────────────────────────────────────────
.loading-bar {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  padding: 16rpx;
  background: rgba(79, 139, 255, 0.95);
  text-align: center;
}
.loading-text {
  color: #fff;
  font-size: 24rpx;
}
</style>
