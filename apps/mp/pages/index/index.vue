<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

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

import type { FavoriteItem } from '@/api/favorites'
import {
  fetchIPOList,
  type IPOItem,
  type IPOStatus,
  type Market,
} from '@/api/ipo'
import IPOCalendar from '@/components/IPOCalendar.vue'
import IPOCard from '@/components/IPOCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useFavoritesStore } from '@/stores/favorites'
import { navigateWithParams } from '@/utils/navigate'

type ViewMode = 'list' | 'calendar'
/**
 * BUG-S6.5-006: 首页加自选 segment-tab。
 * - 'all' 走原有 fetchIPOList 流; 港/A 切 market 一致
 * - 'favorites' 从 favorites store 读, 按当前 market + status 过滤
 */
type ViewSegment = 'all' | 'favorites'

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
const viewSegment = ref<ViewSegment>('all')

const authStore = useAuthStore()
const { loggedIn } = storeToRefs(authStore)
const favStore = useFavoritesStore()
const { items: favoriteItems, loading: favoriteLoading } = storeToRefs(favStore)
const hasMore = computed(() => list.value.length < total.value)

/**
 * BUG-S6.5-006: 自选数据 → IPOItem 兼容映射, 让 IPOCard 直接复用。
 * FavoriteItem 走 LEFT JOIN ipos, 当用户收藏的是 HK seed 还没入库时这些字段为
 * null, 这里给一些合理默认值避免 IPOCard 崩。
 */
function favToIPO(f: FavoriteItem): IPOItem {
  return {
    code: f.code,
    name: f.name ?? f.code,
    market: f.market === 'US' ? 'HK' : f.market,
    industry: f.industry ?? null,
    issue_price: f.issue_price ?? null,
    issue_currency: f.issue_currency ?? null,
    listing_date: f.listing_date ?? null,
    subscribe_start: null,
    subscribe_end: null,
    pe_ratio: null,
    raised_amount: null,
    one_lot_winning_rate: f.one_lot_winning_rate ?? null,
    status: f.status,
    data_source: f.data_source ?? 'favorites',
    updated_at: null,
  }
}

const favoriteList = computed<IPOItem[]>(() => {
  return favoriteItems.value
    .filter((f) => f.market === market.value)
    .filter((f) => statusFilter.value === 'all' || f.status === statusFilter.value)
    .map(favToIPO)
})

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

/**
 * BUG-S6.8-006: 主列表过滤掉 hero 已用 codes, 防止可孚 / 天星等
 * subscribing IPO 在同一页面被渲染两次 (hero 卡 + 主列表 = 视觉重复).
 * 日历视图不去重 — 日历按上市日排, 用户期望看全量.
 */
const heroCodes = computed(() => new Set(todayHotItems.value.map((i) => i.code)))
const mainList = computed<IPOItem[]>(() => {
  if (viewMode.value !== 'list') return list.value
  return list.value.filter((i) => !heroCodes.value.has(i.code))
})

function gotoLogin() {
  uni.navigateTo({ url: '/pages/auth/login' })
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
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode (item.name 是中文 / 跨端差异关键路径)
  void navigateWithParams('/pages/ipo/detail', { code: item.code, name: item.name })
}

/**
 * BUG-S6.5-006: segment 切换。
 * - 'favorites' 未登录态弹 modal 引导登录, 不直接跳避免打断浏览
 * - 已登录则触发 favorites store loadOnce; 缓存机制保证连续切换不重拉
 */
function switchSegment(s: ViewSegment) {
  if (viewSegment.value === s) return
  if (s === 'favorites' && !loggedIn.value) {
    uni.showModal({
      title: '登录后查看自选',
      content: '自选数据需要登录后同步, 现在去登录?',
      confirmText: '去登录',
      success: (r) => {
        if (r.confirm) gotoLogin()
      },
    })
    return
  }
  viewSegment.value = s
  if (s === 'favorites') {
    favStore.loadOnce().catch(() => {
      uni.showToast({ title: '自选加载失败, 请下拉重试', icon: 'none' })
    })
  }
}

onShow(() => {
  if (list.value.length === 0) load(true)
  // 进首页时如果用户已登录, 顺手预热自选 (loadOnce 幂等);
  // 用户切到 favorites segment 时秒显, 不再有 loading 闪烁。
  if (loggedIn.value) {
    favStore.loadOnce().catch(() => {
      // 预热失败不阻塞页面渲染, 用户主动切到 favorites segment 时还会再调
    })
  }
})
onPullDownRefresh(() => {
  if (viewSegment.value === 'favorites') {
    favStore.loadOnce(true).finally(() => uni.stopPullDownRefresh())
  } else {
    load(true)
  }
})
// 仅列表模式 + 全部 IPO 时加载更多; 日历 / 自选都不分页
onReachBottom(() => {
  if (viewSegment.value === 'all' && viewMode.value === 'list' && hasMore.value) {
    load(false)
  }
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view class="hero">
      <view class="hero-left">
        <text class="hero-title">新股智汇</text>
        <text class="hero-subtitle">港 A 股打新 · AI 分析 · 跨境合规</text>
      </view>
      <view class="hero-actions">
        <!--
          BUG-S6.5-004 整组拆解后, hero 右侧只保留登录入口:
          - 📰 市场文章 → IPO 详情页 sub-tab (BUG-S6.5-004a)
          - 🏦 券商对比 → "我的"页 entry (BUG-S6.5-004b)
          - 📊 历史新股 → "中签"页入口卡 (BUG-S6.5-004c)
        -->
        <view v-if="!loggedIn" class="auth-pill" @tap="gotoLogin">
          <text>登录 / 注册</text>
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

    <!-- BUG-S6.5-006: 自选 segment-tab; 全部 IPO ↔ 我的自选 二选一 -->
    <view class="segment">
      <view
        :class="['seg-item', viewSegment === 'all' && 'seg-item-active']"
        @tap="switchSegment('all')"
      >
        <text class="seg-text">全部 IPO</text>
      </view>
      <view
        :class="['seg-item', viewSegment === 'favorites' && 'seg-item-active']"
        @tap="switchSegment('favorites')"
      >
        <text class="seg-text">★ 我的自选</text>
        <text v-if="loggedIn && favoriteItems.length > 0" class="seg-badge">
          {{ favoriteItems.length }}
        </text>
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

    <!-- ========== 全部 IPO 分支 ========== -->
    <template v-if="viewSegment === 'all'">
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
            v-for="item in mainList"
            :key="item.code"
            :item="item"
            @select="openDetail"
          />
          <view v-if="hasMore && loading" class="more-state">
            <text>加载更多...</text>
          </view>
          <view v-else-if="!hasMore && mainList.length > 0" class="more-state">
            <text>—— 已经到底啦 ——</text>
          </view>
        </view>

        <IPOCalendar
          v-else
          :items="list"
          @select="openDetail"
        />
      </template>
    </template>

    <!-- ========== 我的自选分支 (BUG-S6.5-006) ========== -->
    <template v-else>
      <view v-if="favoriteLoading && favoriteList.length === 0" class="state">
        <text>加载中…</text>
      </view>

      <view v-else-if="favoriteList.length === 0" class="state">
        <text v-if="favoriteItems.length === 0">
          还没收藏任何新股, 进 IPO 详情页点击收藏按钮试试
        </text>
        <text v-else>
          {{ market === 'HK' ? '港股' : 'A 股' }}下{{ statusFilter === 'all' ? '' : '该状态' }}暂无自选
        </text>
      </view>

      <view v-else class="list">
        <IPOCard
          v-for="item in favoriteList"
          :key="item.code"
          :item="item"
          @select="openDetail"
        />
        <view class="more-state">
          <text>—— 共 {{ favoriteList.length }} 只 ——</text>
        </view>
      </view>
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
  background: var(--color-bg, #0b1220);
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

/* BUG-S6.5-006: 全部 IPO ↔ 我的自选 segment-tab */
.segment {
  display: flex;
  background: var(--color-surface);
  border-radius: 16rpx;
  padding: 6rpx;
  border: 1rpx solid var(--color-border);
  gap: 4rpx;
}
.seg-item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8rpx;
  padding: 16rpx 20rpx;
  border-radius: 12rpx;
  font-size: 26rpx;
}
.seg-item-active {
  background: var(--color-primary);
}
.seg-text {
  font-size: 26rpx;
  color: var(--color-text-muted);
  font-weight: 500;
}
.seg-item-active .seg-text {
  color: #fff;
  font-weight: 600;
}
.seg-badge {
  font-size: 20rpx;
  min-width: 32rpx;
  padding: 0 10rpx;
  height: 32rpx;
  line-height: 32rpx;
  text-align: center;
  border-radius: 16rpx;
  background: rgba(246, 196, 83, 0.2);
  color: #f6c453;
}
.seg-item-active .seg-badge {
  background: rgba(255, 255, 255, 0.2);
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
