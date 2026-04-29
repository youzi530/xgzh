<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 历史 IPO 列表页 (FE-S4-001).
 *
 * 路径: ``/pages/ipo/historical`` (pages.json 已注册)
 *
 * 功能:
 * 1. 顶部 hero — 行业打新热度入口 (副标题给"看历史 / 找规律"价值锚点)
 * 2. 市场 segment: 港股 / A 股 / 全市场 (默认全市场)
 * 3. 行业 chip 横滚 (静态 8 个常见 + "全部") — 减少首屏 API 调用
 * 4. 排序 segment: 按时间 / 按首日涨幅 / 按中签率 (3 档枚举与后端对齐)
 * 5. 年份范围 picker — uni-picker mode=date 双输入框, 默认 2022-2025
 * 6. 列表 — HistoricalIPOCard 卡片 + 触底加载更多 (size=20)
 * 7. 空态 / 加载态 / 错误态全覆盖
 *
 * 与首页 ``index.vue`` 区别:
 * - 这里走 BE-S4-003 ``/ipos/historical`` (status 强制 listed-only)
 * - 视觉锚点是"上市首日涨幅" (HistoricalIPOCard 大字色块), 而非"申购窗口"
 * - 没有"今日打新"置顶 (历史页没这个语义); 没有日历视图 (历史看时间序意义不大)
 *
 * 后续:
 * - FE-S4-002 IPO 详情页接 uCharts 散点图 (复用 ``/ipos/{code}/peer-aggregate``)
 * - FE-S4-003 顶部 FAB → 触发 BE-S4-004 ``POST /agent/historical-pattern`` SSE
 */

import { onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  fetchHistoricalIPOList,
  type HistoricalIPOItem,
  type HistoricalSortBy,
  type Market,
} from '@/api/ipo'
import HistoricalIPOCard from '@/components/HistoricalIPOCard.vue'
import { navigateWithParams } from '@/utils/navigate'

type MarketKey = Market | 'all'

interface MarketOption {
  key: MarketKey
  label: string
}

interface SortOption {
  key: HistoricalSortBy
  label: string
}

interface IndustryOption {
  key: string | null
  label: string
}

const MARKET_OPTIONS: MarketOption[] = [
  { key: 'all', label: '全市场' },
  { key: 'HK', label: '港股' },
  { key: 'A', label: 'A 股' },
]

const SORT_OPTIONS: SortOption[] = [
  { key: 'listing_date', label: '按时间' },
  { key: 'first_day_change_pct', label: '按首日涨幅' },
  { key: 'one_lot_winning_rate', label: '按中签率' },
]

/**
 * 静态 8 个常见行业 (与 BE-S4-002 backfill ``_INDUSTRIES`` 配置对齐):
 * 互联网 / 医药 / 新能源 / 消费 / 金融 / 科技 / AI / 半导体. "全部" 不传 industry.
 *
 * 后续如要做"行业 chip 动态化", 可加 ``GET /ipos/industries`` 端点;
 * 当前静态 8 个已覆盖 ~85% 历史 IPO, MVP 够用.
 */
const INDUSTRY_OPTIONS: IndustryOption[] = [
  { key: null, label: '全部' },
  { key: '互联网', label: '互联网' },
  { key: '医药', label: '医药' },
  { key: '新能源', label: '新能源' },
  { key: '消费', label: '消费' },
  { key: '金融', label: '金融' },
  { key: '科技', label: '科技' },
  { key: 'AI', label: 'AI' },
  { key: '半导体', label: '半导体' },
]

const PAGE_SIZE = 20
// 默认年份: 近 3 年 (2022 ~ 2025), 既不太早 (老数据偏冷) 也不太短 (样本不足 < 5)
const DEFAULT_YEAR_FROM = 2022
const DEFAULT_YEAR_TO = 2025
// 年份选择器范围 (前端硬约束; 后端 ge=1990 le=2100 是软约束)
const YEAR_PICKER_MIN = 2010
const YEAR_PICKER_MAX = new Date().getFullYear()

const list = ref<HistoricalIPOItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<string>('')

const market = ref<MarketKey>('all')
const sortBy = ref<HistoricalSortBy>('listing_date')
const industry = ref<string | null>(null)
const yearFrom = ref<number>(DEFAULT_YEAR_FROM)
const yearTo = ref<number>(DEFAULT_YEAR_TO)

// PE-S4-001 长列表内存释放: 单次加载累计 hardcap 200 条 (= 10 页 × 20).
// 超过后 onReachBottom 静默拒绝 + toast 引导用户下拉刷新; 防止 dev DB 600+ 行
// 用户狂滚到 list.length=600 内存爆 (单条 ~250B JSON × 600 = ~150KB, MP 上 vue 反应式
// + DOM 节点开销可达 5MB+). 阈值 200 来自 spec/07 §6.2 性能预算.
const MAX_LIST_LENGTH = 200
const hasMore = computed(
  () => list.value.length < total.value && list.value.length < MAX_LIST_LENGTH,
)
const hitHardCap = computed(() => list.value.length >= MAX_LIST_LENGTH)
const yearRangeText = computed(() => `${yearFrom.value} - ${yearTo.value}`)

const yearOptions = computed<number[]>(() => {
  const arr: number[] = []
  for (let y = YEAR_PICKER_MAX; y >= YEAR_PICKER_MIN; y--) arr.push(y)
  return arr
})
const yearOptionLabels = computed<string[]>(() =>
  yearOptions.value.map((y) => String(y)),
)

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
    const resp = await fetchHistoricalIPOList({
      market: market.value === 'all' ? undefined : market.value,
      industry: industry.value ?? undefined,
      year_from: yearFrom.value,
      year_to: yearTo.value,
      sort_by: sortBy.value,
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

function selectMarket(m: MarketKey) {
  if (market.value === m) return
  market.value = m
  load(true)
}

function selectSort(s: HistoricalSortBy) {
  if (sortBy.value === s) return
  sortBy.value = s
  load(true)
}

function selectIndustry(i: string | null) {
  if (industry.value === i) return
  industry.value = i
  load(true)
}

function onYearFromChange(e: { detail: { value: number | string } }) {
  const idx = Number(e.detail.value)
  const y = yearOptions.value[idx]
  if (y == null) return
  // 守卫: from > to 时自动把 to 抬到 from
  if (y > yearTo.value) yearTo.value = y
  yearFrom.value = y
  load(true)
}

function onYearToChange(e: { detail: { value: number | string } }) {
  const idx = Number(e.detail.value)
  const y = yearOptions.value[idx]
  if (y == null) return
  if (y < yearFrom.value) yearFrom.value = y
  yearTo.value = y
  load(true)
}

const yearFromIdx = computed(() => yearOptions.value.indexOf(yearFrom.value))
const yearToIdx = computed(() => yearOptions.value.indexOf(yearTo.value))

function openDetail(item: HistoricalIPOItem) {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode, 不再手动 ``encodeURIComponent``
  void navigateWithParams('/pages/ipo/detail', { code: item.code, name: item.name })
}

function gotoBack() {
  uni.navigateBack({ delta: 1 })
}

/**
 * FE-S4-003 AI 报告入口: 透传当前筛选条件 (industry / market / year_from / year_to),
 * 报告页 onLoad 自动填表; 用户进去直接点"生成"即可, 不用重选条件.
 */
function gotoAIReport() {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode (industry 中文 / market 枚举 / 年份)
  void navigateWithParams('/pages/ipo/historical-pattern', {
    industry: industry.value || undefined,
    market: market.value !== 'all' ? market.value : undefined,
    year_from: yearFrom.value,
    year_to: yearTo.value,
  })
}

onShow(() => {
  if (list.value.length === 0) load(true)
})
onPullDownRefresh(() => load(true))
onReachBottom(() => {
  // PE-S4-001: 已到 hardcap 静默拒, 防"用户一直滚一直 fetch" 流量浪费 + 内存膨胀
  if (hitHardCap.value) {
    uni.showToast({
      title: `已加载 ${MAX_LIST_LENGTH} 条上限, 下拉刷新查看最新`,
      icon: 'none',
      duration: 2500,
    })
    return
  }
  if (hasMore.value) load(false)
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── hero ─── -->
    <view class="hero">
      <view class="hero-back" hover-class="hero-back-hover" :hover-stay-time="80" @tap="gotoBack">
        <text class="hero-back-icon">‹</text>
      </view>
      <view class="hero-text">
        <text class="hero-title">历史新股</text>
        <text class="hero-subtitle">看 IPO 涨跌规律 · 找打新参考</text>
      </view>
    </view>

    <!-- ─── market segment ─── -->
    <view class="seg">
      <view
        v-for="m in MARKET_OPTIONS"
        :key="m.key"
        :class="['seg-item', market === m.key && 'seg-item-active']"
        hover-class="seg-item-hover"
        :hover-stay-time="80"
        @tap="selectMarket(m.key)"
      >
        <text class="seg-text">{{ m.label }}</text>
      </view>
    </view>

    <!-- ─── 行业 chip 横滚 ─── -->
    <scroll-view scroll-x class="chips" :show-scrollbar="false">
      <view
        v-for="i in INDUSTRY_OPTIONS"
        :key="`ind-${i.key ?? 'all'}`"
        :class="['chip', industry === i.key && 'chip-active']"
        hover-class="chip-hover"
        :hover-stay-time="80"
        @tap="selectIndustry(i.key)"
      >
        <text class="chip-text">{{ i.label }}</text>
      </view>
    </scroll-view>

    <!-- ─── 排序 chip 横滚 ─── -->
    <scroll-view scroll-x class="chips" :show-scrollbar="false">
      <view
        v-for="o in SORT_OPTIONS"
        :key="`sort-${o.key}`"
        :class="['chip', sortBy === o.key && 'chip-active']"
        hover-class="chip-hover"
        :hover-stay-time="80"
        @tap="selectSort(o.key)"
      >
        <text class="chip-text">{{ o.label }}</text>
      </view>
    </scroll-view>

    <!-- ─── 年份范围 picker (双 picker; uni-picker mode=selector) ─── -->
    <view class="year-bar">
      <text class="year-label">年份范围</text>
      <picker
        mode="selector"
        :range="yearOptionLabels"
        :value="yearFromIdx"
        @change="onYearFromChange"
      >
        <view class="year-input">
          <text class="year-input-text">{{ yearFrom }}</text>
        </view>
      </picker>
      <text class="year-sep">—</text>
      <picker
        mode="selector"
        :range="yearOptionLabels"
        :value="yearToIdx"
        @change="onYearToChange"
      >
        <view class="year-input">
          <text class="year-input-text">{{ yearTo }}</text>
        </view>
      </picker>
      <text class="year-summary">{{ total }} 条</text>
    </view>

    <!-- ─── 列表 / 空态 / loading ─── -->
    <view v-if="loading && list.length === 0" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="error" class="state-block state-error">
      <text class="state-emoji">⚠️</text>
      <text class="state-text">加载失败: {{ error }}</text>
      <view class="retry" hover-class="retry-hover" :hover-stay-time="80" @tap="load(true)">
        <text class="retry-text">点击重试</text>
      </view>
    </view>

    <view v-else-if="list.length === 0 && !loading" class="state-block">
      <text class="state-emoji">🔍</text>
      <text class="state-text">没有符合条件的历史 IPO</text>
      <text class="state-sub">试试切换行业 / 调宽年份范围</text>
    </view>

    <view v-else class="list">
      <HistoricalIPOCard
        v-for="item in list"
        :key="item.code"
        :item="item"
        @select="openDetail"
      />
      <view v-if="loading && list.length > 0" class="more-state">
        <text>加载更多…</text>
      </view>
      <view v-else-if="!hasMore && list.length > 0" class="more-state">
        <text>—— 已加载全部 {{ list.length }} 条 ({{ yearRangeText }}) ——</text>
      </view>
    </view>

    <view class="footer">
      <text class="footer-disclaimer">历史数据仅供参考, 不构成投资建议</text>
    </view>

    <!-- FE-S4-003: AI 历史规律分析报告 FAB; 透传当前筛选 industry/market/year 直入报告页 -->
    <view
      class="ai-fab"
      hover-class="ai-fab-hover"
      :hover-stay-time="80"
      @tap="gotoAIReport"
    >
      <text class="ai-fab-icon">🤖</text>
      <text class="ai-fab-text">AI 看规律</text>
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
  gap: 20rpx;
}

.hero {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 8rpx 0 0;
}
.hero-back {
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
  display: flex;
  align-items: center;
  justify-content: center;
}
.hero-back-hover {
  background: rgba(255, 255, 255, 0.16);
}
.hero-back-icon {
  font-size: 36rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1;
}
.hero-text {
  flex: 1;
  min-width: 0;
}
.hero-title {
  display: block;
  font-size: 40rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.hero-subtitle {
  display: block;
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.seg {
  display: flex;
  background: var(--color-surface, #131a2c);
  border-radius: 999rpx;
  padding: 4rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.seg-item {
  flex: 1;
  padding: 12rpx 0;
  border-radius: 999rpx;
  text-align: center;
}
.seg-item-active {
  background: var(--color-primary, #4f8bff);
}
.seg-item-hover {
  background: rgba(255, 255, 255, 0.08);
}
.seg-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.seg-item-active .seg-text {
  color: #fff;
  font-weight: 600;
}

.chips {
  white-space: nowrap;
}
.chip {
  display: inline-block;
  padding: 8rpx 24rpx;
  margin-right: 12rpx;
  border-radius: 999rpx;
  background: var(--color-surface, #131a2c);
  color: var(--color-text-muted, #94a3b8);
  font-size: 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.chip-active {
  background: rgba(246, 196, 83, 0.15);
  border-color: rgba(246, 196, 83, 0.4);
}
.chip-active .chip-text {
  color: #f6c453;
}
.chip-hover {
  background: rgba(255, 255, 255, 0.06);
}

.year-bar {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 8rpx 4rpx;
}
.year-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.year-input {
  padding: 8rpx 24rpx;
  border-radius: 12rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.year-input-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  font-feature-settings: 'tnum';
}
.year-sep {
  color: var(--color-text-muted, #94a3b8);
  font-size: 24rpx;
}
.year-summary {
  margin-left: auto;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.state-block {
  text-align: center;
  padding: 80rpx 24rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.state-emoji {
  font-size: 48rpx;
}
.state-text {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
.state-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.state-error .state-text {
  color: var(--color-danger, #ef4444);
}
.retry {
  margin-top: 16rpx;
  padding: 12rpx 32rpx;
  border-radius: 8rpx;
  background: var(--color-primary, #4f8bff);
}
.retry-hover {
  opacity: 0.85;
}
.retry-text {
  color: #fff;
  font-size: 24rpx;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.more-state {
  text-align: center;
  padding: 24rpx 0;
  color: var(--color-text-muted, #94a3b8);
  font-size: 22rpx;
}

.footer {
  margin-top: 16rpx;
  padding-top: 24rpx;
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  text-align: center;
}
.footer-disclaimer {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.8;
}

/* FE-S4-003: AI 报告 FAB; fixed 右下角, 不挡列表 */
.ai-fab {
  position: fixed;
  right: 24rpx;
  bottom: calc(40rpx + env(safe-area-inset-bottom));
  display: flex;
  align-items: center;
  gap: 8rpx;
  padding: 18rpx 28rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #7c3aed);
  box-shadow: 0 8rpx 24rpx rgba(79, 139, 255, 0.32);
  z-index: 99;
}
.ai-fab-hover {
  opacity: 0.85;
}
.ai-fab-icon {
  font-size: 28rpx;
  line-height: 1;
}
.ai-fab-text {
  font-size: 24rpx;
  font-weight: 600;
  color: #fff;
  letter-spacing: 1rpx;
}
</style>
