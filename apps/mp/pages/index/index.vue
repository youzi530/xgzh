<script setup lang="ts">
/**
 * 首页 IPO 列表 (FE-004 升级版).
 *
 * 升级目标:
 * 1. "今日打新"置顶卡: 列表模式下把当日仍在申购窗口的 IPO 放最上面
 * 2. 主区双视图切换: 瀑布流 (默认) ↔ 打新日历
 * 3. status 筛选 chip: 全部 / 申购中 / 待上市 / 已上市
 * 4. 分页加载更多: onReachBottom 触发, total 守卫不重复拉
 * 5. 数据来源 footer: aggregate items 里的 data_source 字段
 *
 * 视觉: hero (品牌区 + 登录入口) → market tab (HK/A) → status chip → view 切换 →
 *       今日打新 (列表模式) → 主列表 / 日历 → footer
 */

import { onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import type { UserPublic } from '@/api/auth'
import {
  fetchIPOList,
  type IPOItem,
  type IPOStatus,
  type Market,
} from '@/api/ipo'
import IPOCalendar from '@/components/IPOCalendar.vue'
import IPOCard from '@/components/IPOCard.vue'
import { useAuthStore } from '@/stores/auth'

type ViewMode = 'list' | 'calendar'

interface StatusFilter {
  key: 'all' | IPOStatus
  label: string
}

const STATUS_FILTERS: StatusFilter[] = [
  { key: 'all', label: '全部' },
  { key: 'subscribing', label: '申购中' },
  { key: 'upcoming', label: '待上市' },
  { key: 'listed', label: '已上市' },
]

const PAGE_SIZE = 20

const list = ref<IPOItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<string>('')

const market = ref<Market>('HK')
const statusFilter = ref<'all' | IPOStatus>('all')
const viewMode = ref<ViewMode>('list')

const authStore = useAuthStore()
const { user, loggedIn } = storeToRefs(authStore)
const currentUser = computed<UserPublic | null>(() => user.value)

const hasMore = computed(() => list.value.length < total.value)

const dataSourceText = computed(() => {
  const sources = new Set<string>()
  for (const item of list.value) {
    if (item.data_source) sources.add(item.data_source)
  }
  if (sources.size === 0) return '数据来源待补'
  return `数据来源：${Array.from(sources).join(' / ')}`
})

/**
 * 今日打新: 列表里 status === 'subscribing' (后端已经按 ``listing_date DESC NULLS LAST``
 * 排序; 这里不再二次排序, 仅截前 3 个用于 hero 卡; 与日历视图共用同一份 list 数据)。
 */
const todayHotItems = computed<IPOItem[]>(() => {
  return list.value.filter((i) => i.status === 'subscribing').slice(0, 3)
})

function nicknameInitial(u: UserPublic): string {
  if (u.nickname && u.nickname.length > 0) return u.nickname.slice(0, 1)
  return u.invite_code.slice(0, 1)
}

function gotoLogin() {
  uni.navigateTo({ url: '/pages/auth/login' })
}

function gotoProfile() {
  uni.navigateTo({ url: '/pages/me/index' })
}

async function load(reset = false) {
  if (loading.value) return
  if (reset) {
    page.value = 1
    list.value = []
    total.value = 0
  } else if (!hasMore.value && list.value.length > 0) {
    return
  }
  loading.value = true
  error.value = ''
  try {
    const resp = await fetchIPOList(market.value, {
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      page: page.value,
      size: PAGE_SIZE,
    })
    list.value = reset ? resp.items : [...list.value, ...resp.items]
    total.value = resp.total
    page.value += 1
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function switchMarket(m: Market) {
  if (market.value === m) return
  market.value = m
  load(true)
}

function switchStatus(s: 'all' | IPOStatus) {
  if (statusFilter.value === s) return
  statusFilter.value = s
  load(true)
}

function switchView(v: ViewMode) {
  viewMode.value = v
}

function openDetail(item: IPOItem) {
  uni.navigateTo({
    url: `/pages/ipo/detail?code=${encodeURIComponent(item.code)}&name=${encodeURIComponent(item.name)}`,
  })
}

function gotoArticles() {
  uni.navigateTo({ url: '/pages/article/index' })
}

function gotoBrokers() {
  uni.navigateTo({ url: '/pages/broker/index' })
}

// FE-S4-001: 历史 IPO 列表入口 (新股 → 历史规律, 给"看打新参考"用户钩子)
function gotoHistorical() {
  uni.navigateTo({ url: '/pages/ipo/historical' })
}

onShow(() => {
  if (list.value.length === 0) load(true)
})
onPullDownRefresh(() => load(true))
// 仅列表模式下加载更多; 日历模式所有数据已分组完, 翻不到下一页对用户没意义
onReachBottom(() => {
  if (viewMode.value === 'list' && hasMore.value) load(false)
})
</script>

<template>
  <view class="page">
    <view class="hero">
      <view class="hero-left">
        <text class="hero-title">新股智汇</text>
        <text class="hero-subtitle">港 A 股打新 · AI 分析 · 跨境合规</text>
      </view>
      <view class="hero-actions">
        <!-- FE-S3-001: 市场文章入口; tabBar 落地前以小入口形式暴露 -->
        <view
          class="hero-icon-btn"
          hover-class="hero-icon-btn-hover"
          :hover-stay-time="80"
          @tap="gotoArticles"
        >
          <text class="hero-icon">📰</text>
        </view>
        <!-- FE-S3-003: 券商对比入口 -->
        <view
          class="hero-icon-btn"
          hover-class="hero-icon-btn-hover"
          :hover-stay-time="80"
          @tap="gotoBrokers"
        >
          <text class="hero-icon">🏦</text>
        </view>
        <!-- FE-S4-001: 历史新股入口 -->
        <view
          class="hero-icon-btn"
          hover-class="hero-icon-btn-hover"
          :hover-stay-time="80"
          @tap="gotoHistorical"
        >
          <text class="hero-icon">📊</text>
        </view>
        <view v-if="!loggedIn" class="auth-pill" @tap="gotoLogin">
          <text>登录 / 注册</text>
        </view>
        <view v-else class="auth-avatar" @tap="gotoProfile">
          <text class="auth-avatar-text">{{ nicknameInitial(currentUser!) }}</text>
        </view>
      </view>
    </view>

    <view class="bar">
      <view class="market-tabs">
        <view :class="['mtab', market === 'HK' && 'mtab-active']" @tap="switchMarket('HK')">港股</view>
        <view :class="['mtab', market === 'A' && 'mtab-active']" @tap="switchMarket('A')">A 股</view>
      </view>
      <view class="view-toggle">
        <view :class="['vt', viewMode === 'list' && 'vt-active']" @tap="switchView('list')">列表</view>
        <view :class="['vt', viewMode === 'calendar' && 'vt-active']" @tap="switchView('calendar')">日历</view>
      </view>
    </view>

    <scroll-view scroll-x class="status-chips" :show-scrollbar="false">
      <view
        v-for="s in STATUS_FILTERS"
        :key="s.key"
        :class="['schip', statusFilter === s.key && 'schip-active']"
        @tap="switchStatus(s.key)"
      >
        <text>{{ s.label }}</text>
      </view>
    </scroll-view>

    <!-- 今日打新置顶 (仅列表模式) -->
    <view
      v-if="viewMode === 'list' && todayHotItems.length > 0"
      class="today-section"
    >
      <view class="today-head">
        <text class="today-title">今日打新</text>
        <text class="today-subtitle">{{ todayHotItems.length }} 只正在申购中</text>
      </view>
      <view class="today-list">
        <IPOCard
          v-for="item in todayHotItems"
          :key="`hero-${item.code}`"
          :item="item"
          variant="hero"
          @select="openDetail"
        />
      </view>
    </view>

    <!-- 主体: list / calendar -->
    <view v-if="loading && list.length === 0" class="state">
      <text>加载中…</text>
    </view>

    <view v-else-if="error" class="state state-error">
      <text>加载失败：{{ error }}</text>
      <view class="retry" @tap="load(true)">点击重试</view>
    </view>

    <view v-else-if="list.length === 0" class="state">
      <text>暂无 {{ statusFilter === 'all' ? '' : '该状态下的 ' }}IPO</text>
    </view>

    <template v-else>
      <view v-if="viewMode === 'list'" class="list">
        <IPOCard
          v-for="item in list"
          :key="item.code"
          :item="item"
          @select="openDetail"
        />
        <view v-if="hasMore && loading" class="more-state">
          <text>加载更多...</text>
        </view>
        <view v-else-if="!hasMore && list.length > 0" class="more-state">
          <text>—— 已经到底啦 ——</text>
        </view>
      </view>

      <IPOCalendar
        v-else
        :items="list"
        @select="openDetail"
      />
    </template>

    <view class="footer">
      <text class="footer-source">{{ dataSourceText }}</text>
      <text class="footer-disclaimer">数据仅供参考，不构成投资建议</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 80rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}
.hero {
  padding: 16rpx 8rpx 0;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}
.hero-left {
  flex: 1;
  min-width: 0;
}
.hero-title {
  display: block;
  font-size: 48rpx;
  font-weight: 700;
  color: var(--color-text);
}
.hero-subtitle {
  display: block;
  margin-top: 8rpx;
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.hero-actions {
  flex-shrink: 0;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}
.hero-icon-btn {
  width: 64rpx;
  height: 64rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-icon-btn-hover {
  background: rgba(255, 255, 255, 0.16);
}
.hero-icon {
  font-size: 32rpx;
  line-height: 1;
}
.auth-pill {
  flex-shrink: 0;
  padding: 12rpx 24rpx;
  border-radius: 999rpx;
  font-size: 24rpx;
  color: var(--color-primary);
  background: rgba(79, 139, 255, 0.1);
  border: 1rpx solid rgba(79, 139, 255, 0.3);
}
.auth-avatar {
  flex-shrink: 0;
  width: 64rpx;
  height: 64rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
  display: flex;
  align-items: center;
  justify-content: center;
}
.auth-avatar-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}

.bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}
.market-tabs {
  display: flex;
  gap: 12rpx;
}
.mtab {
  padding: 10rpx 28rpx;
  border-radius: 999rpx;
  font-size: 26rpx;
  background: var(--color-surface);
  color: var(--color-text-muted);
  border: 1rpx solid var(--color-border);
}
.mtab-active {
  background: var(--color-primary);
  color: #fff;
  border-color: transparent;
}
.view-toggle {
  display: flex;
  background: var(--color-surface);
  border-radius: 999rpx;
  padding: 4rpx;
  border: 1rpx solid var(--color-border);
}
.vt {
  padding: 8rpx 24rpx;
  border-radius: 999rpx;
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.vt-active {
  background: var(--color-primary);
  color: #fff;
}

.status-chips {
  white-space: nowrap;
  padding: 4rpx 0;
}
.schip {
  display: inline-block;
  padding: 8rpx 24rpx;
  margin-right: 12rpx;
  border-radius: 999rpx;
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: 24rpx;
  border: 1rpx solid var(--color-border);
}
.schip-active {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
  border-color: rgba(246, 196, 83, 0.4);
}

.today-section {
  background: rgba(246, 196, 83, 0.05);
  border: 1rpx solid rgba(246, 196, 83, 0.18);
  border-radius: 24rpx;
  padding: 20rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.today-head {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}
.today-title {
  font-size: 30rpx;
  font-weight: 700;
  color: #f6c453;
}
.today-subtitle {
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.today-list {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.state {
  text-align: center;
  padding: 80rpx 0;
  color: var(--color-text-muted);
  font-size: 28rpx;
}
.state-error {
  color: var(--color-danger);
}
.retry {
  margin-top: 16rpx;
  display: inline-block;
  padding: 12rpx 32rpx;
  border-radius: 8rpx;
  background: var(--color-primary);
  color: #fff;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.more-state {
  text-align: center;
  padding: 24rpx 0;
  color: var(--color-text-muted);
  font-size: 22rpx;
}

.footer {
  margin-top: 16rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  text-align: center;
}
.footer-source {
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.footer-disclaimer {
  font-size: 22rpx;
  color: var(--color-text-muted);
  opacity: 0.8;
}
</style>
