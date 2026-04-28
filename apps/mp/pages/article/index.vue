<script setup lang="ts">
/**
 * 文章列表 Tab UI (FE-S3-001).
 *
 * 路由: ``/pages/article/index``
 *
 * 模块:
 * 1. **顶部 sticky 筛选条**: market 分段 (HK / A / 全部) + sentiment chip
 *    (全部 / 看多 / 中性 / 看空) + sort chip (最新 / 热度)
 * 2. **TL;DR 入口悬浮按钮** (spec/03 §模块二 1.2): 顶部右侧悬浮 "💡 TL;DR" 圆形 FAB,
 *    点 → 跳 ``/pages/article/tldr?scope=market&value=${market}`` (FE-S3-002 实现)
 * 3. **文章卡片瀑布流**: 复用 ``ArticleCard`` 组件; 触底加载更多
 * 4. **下拉刷新**: ``onPullDownRefresh`` 走 ``load(reset=true)``; pages.json 已开
 *    ``enablePullDownRefresh``
 * 5. **空态**: 当前筛选无文章 → 显示 spec/03 §模块二 "暂未抓取到 XX 公司公开文章" 文案
 * 6. **错误条**: 5xx 错误顶部红色 banner + 重试按钮
 *
 * 设计取舍:
 *
 * - **筛选切换 reset 列表**: 切 market / sentiment / sort 时 ``load(reset=true)`` 把
 *   ``list = [], page = 1``, 然后重拉. 不复用旧数据 (筛选语义变了, 旧数据不属于
 *   新 scope). 比"叠加新数据" 用户体验清爽
 *
 * - **不维护本地 5 min 缓存 ref**: spec 提到 "切 tab 不重拉 (5 min TTL)", 但 BE 已
 *   在 articles_list_cache 走 Redis 5min, 同样参数命中同一缓存。前端再做 ref 缓存
 *   反增 stale 风险 (缓存命中后用户撞 quota → 升级 → 想看新内容仍显旧). 直接每次
 *   load 走 BE, 让缓存层级单一
 *
 * - **触底分页用 onReachBottom 而非 IntersectionObserver**: uni-app 内置 page-level
 *   ``onReachBottom`` 跨 H5 / MP-WEIXIN / App 都跑得通, 不需要自己造 IO 轮询; 复用
 *   首页 IPO 列表分页同款 pattern
 *
 * - **TL;DR FAB 仅 sticky 不悬浮在卡片上方**: spec 说"列表页顶部悬浮按钮", 实际
 *   把 FAB 放筛选条右侧, 与筛选 chip 同水平 sticky, 不另开"右下角 FAB" — 后者
 *   会挡住"加载更多"和最后一条文章; 顶部 sticky 用户每次滚到顶都能看到入口
 *
 * - **关联 IPO chip 跳 IPO 详情**: 走 ``/pages/ipo/detail?code=${code}`` (FE-005);
 *   走 ``@tap.stop`` 防冒泡到卡片自己的 click 事件
 */

import { onLoad, onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  fetchArticleList,
  type ArticleListItem,
  type ListMarketFilter,
  type Sentiment,
  type SortBy,
} from '@/api/article'
import ArticleCard from '@/components/ArticleCard.vue'

const PAGE_SIZE = 20

// ─── 筛选 state ─────────────────────────────────────────────
const market = ref<ListMarketFilter>('all')
const sentiment = ref<Sentiment | 'all'>('all')
const sortBy = ref<SortBy>('published_at')

// ─── 列表 state ─────────────────────────────────────────────
const list = ref<ArticleListItem[]>([])
const total = ref(0)
const page = ref(1)
const loading = ref(false)
const error = ref<string>('')

const hasMore = computed(() => list.value.length < total.value)

// ─── 筛选选项 ──────────────────────────────────────────────
interface MarketOption {
  key: ListMarketFilter
  label: string
}
const MARKET_OPTIONS: MarketOption[] = [
  { key: 'all', label: '全部' },
  { key: 'HK', label: '港股' },
  { key: 'A', label: 'A 股' },
]

interface SentimentOption {
  key: Sentiment | 'all'
  label: string
}
const SENTIMENT_OPTIONS: SentimentOption[] = [
  { key: 'all', label: '全部情感' },
  { key: 'bullish', label: '看多' },
  { key: 'neutral', label: '中性' },
  { key: 'bearish', label: '看空' },
]

interface SortOption {
  key: SortBy
  label: string
}
const SORT_OPTIONS: SortOption[] = [
  { key: 'published_at', label: '最新' },
  { key: 'hot_score', label: '热度' },
]

// ─── load ────────────────────────────────────────────────────

async function load(reset = false) {
  if (loading.value) return
  if (reset) {
    page.value = 1
    list.value = []
    total.value = 0
    error.value = ''
  } else if (!hasMore.value && list.value.length > 0) {
    return
  }
  loading.value = true
  try {
    const resp = await fetchArticleList({
      market: market.value,
      sentiment: sentiment.value,
      sort_by: sortBy.value,
      page: page.value,
      size: PAGE_SIZE,
    })
    list.value = reset ? resp.items : [...list.value, ...resp.items]
    total.value = resp.total
    page.value += 1
  } catch (e) {
    console.warn('[article-list] load failed', e)
    error.value = '加载失败, 请下拉刷新或检查网络'
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

// ─── 筛选切换 ───────────────────────────────────────────────
function selectMarket(m: ListMarketFilter) {
  if (market.value === m || loading.value) return
  market.value = m
  void load(true)
}

function selectSentiment(s: Sentiment | 'all') {
  if (sentiment.value === s || loading.value) return
  sentiment.value = s
  void load(true)
}

function selectSort(s: SortBy) {
  if (sortBy.value === s || loading.value) return
  sortBy.value = s
  void load(true)
}

// ─── 跳转 ───────────────────────────────────────────────────
function onArticleClick(articleId: string) {
  uni.navigateTo({
    url: `/pages/article/detail?article_id=${encodeURIComponent(articleId)}`,
    fail: () => {
      // detail 页 (FE-S3-002) 还未注册时, 直接跳原文 URL 兜底; 这里先 toast 提示
      uni.showToast({ title: '详情页即将上线', icon: 'none' })
    },
  })
}

function onIpoClick(code: string) {
  uni.navigateTo({ url: `/pages/ipo/detail?code=${encodeURIComponent(code)}` })
}

function gotoTldr() {
  // FE-S3-002 实现; 这里先 toast 占位, scope 走 market 当前值
  // 真实页面 query 形如 ``?scope=market&scope_value=HK`` (BE-S3-005 协议)
  uni.showToast({ title: 'TL;DR 抽屉即将上线', icon: 'none' })
}

// ─── lifecycle ──────────────────────────────────────────────
onLoad(() => {
  void load(true)
})

onShow(() => {
  // 列表为空 (例如错误 / 第一次加载失败后切回) 时尝试重新加载;
  // 已有数据不重拉, 避免每次切回小程序都浪费一次 BE 请求
  if (list.value.length === 0 && !loading.value) {
    void load(true)
  }
})

onPullDownRefresh(() => {
  void load(true)
})

onReachBottom(() => {
  void load(false)
})
</script>

<template>
  <view class="page">
    <!-- ─── 错误 banner ─── -->
    <view v-if="error" class="err-banner">
      <text class="err-text">{{ error }}</text>
      <view class="err-retry" hover-class="err-retry-hover" :hover-stay-time="80" @tap="load(true)">
        <text class="err-retry-text">重试</text>
      </view>
    </view>

    <!-- ─── sticky 筛选 + TL;DR FAB ─── -->
    <view class="filter-bar">
      <!-- market segment -->
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

      <!-- 横滚 chip 行: sentiment + sort -->
      <scroll-view scroll-x class="chips" :show-scrollbar="false">
        <view
          v-for="s in SENTIMENT_OPTIONS"
          :key="`sent-${s.key}`"
          :class="['chip', sentiment === s.key && 'chip-active']"
          hover-class="chip-hover"
          :hover-stay-time="80"
          @tap="selectSentiment(s.key)"
        >
          <text class="chip-text">{{ s.label }}</text>
        </view>
        <view class="chip-divider" />
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
        <!-- TL;DR FAB 内联 (横滚最右侧, 自带视觉重量) -->
        <view class="chip-divider" />
        <view
          class="chip chip-tldr"
          hover-class="chip-tldr-hover"
          :hover-stay-time="80"
          @tap="gotoTldr"
        >
          <text class="chip-text chip-tldr-text">💡 TL;DR</text>
        </view>
      </scroll-view>
    </view>

    <!-- ─── 列表 / 空态 / loading ─── -->
    <view v-if="loading && list.length === 0" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="list.length === 0 && !loading && !error" class="state-block">
      <text class="state-emoji">🔍</text>
      <text class="state-text">暂未抓取到符合条件的文章</text>
      <text class="state-sub">试试切换筛选, 或下拉刷新</text>
    </view>

    <view v-else class="list">
      <ArticleCard
        v-for="article in list"
        :key="article.article_id"
        :article="article"
        @click="onArticleClick"
        @ipo-click="onIpoClick"
      />

      <!-- 触底加载提示 -->
      <view v-if="loading && list.length > 0" class="loading-more">
        <text class="loading-more-text">加载更多…</text>
      </view>
      <view v-else-if="!hasMore && list.length > 0" class="list-end">
        <text class="list-end-text">— 已加载全部 {{ list.length }} 篇 —</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  padding-bottom: 40rpx;
}

/* ─── 错误 banner ─── */
.err-banner {
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 16rpx 24rpx;
  background: rgba(239, 68, 68, 0.12);
  border-bottom: 1rpx solid rgba(239, 68, 68, 0.4);
}
.err-text {
  flex: 1;
  font-size: 22rpx;
  color: #ef4444;
}
.err-retry {
  flex-shrink: 0;
  padding: 8rpx 24rpx;
  border-radius: 999rpx;
  border: 1rpx solid rgba(239, 68, 68, 0.5);
}
.err-retry-hover {
  background: rgba(239, 68, 68, 0.2);
}
.err-retry-text {
  font-size: 22rpx;
  color: #ef4444;
  font-weight: 600;
}

/* ─── 筛选 bar (sticky) ─── */
.filter-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(11, 18, 32, 0.95);
  backdrop-filter: blur(8rpx);
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  padding: 16rpx 24rpx 12rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.seg {
  display: flex;
  flex-direction: row;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
  padding: 4rpx;
  align-self: flex-start;
}

.seg-item {
  padding: 12rpx 28rpx;
  border-radius: 999rpx;
}

.seg-item-hover {
  background: rgba(255, 255, 255, 0.06);
}

.seg-item-active {
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.18);
}

.seg-text {
  font-size: 24rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.seg-item-active .seg-text {
  color: #ffffff;
}

/* ─── 横滚 chips ─── */
.chips {
  white-space: nowrap;
  /* 抵消 padding 让横滚卡片能贴边滚 */
  margin: 0 -24rpx;
  padding: 4rpx 24rpx;
}

.chip {
  display: inline-flex;
  vertical-align: middle;
  margin-right: 12rpx;
  padding: 10rpx 20rpx;
  border-radius: 999rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
}

.chip-hover {
  background: rgba(255, 255, 255, 0.06);
}

.chip-active {
  background: rgba(79, 139, 255, 0.16);
  border-color: rgba(79, 139, 255, 0.5);
}

.chip-text {
  font-size: 22rpx;
  color: var(--color-text, #e2e8f0);
  font-weight: 500;
}

.chip-active .chip-text {
  color: #4f8bff;
  font-weight: 700;
}

.chip-divider {
  display: inline-block;
  width: 2rpx;
  height: 28rpx;
  margin: 4rpx 16rpx 4rpx 4rpx;
  vertical-align: middle;
  background: rgba(255, 255, 255, 0.12);
}

/* ─── TL;DR FAB chip (金色突出) ─── */
.chip-tldr {
  background: rgba(246, 196, 83, 0.14);
  border-color: rgba(246, 196, 83, 0.5);
}
.chip-tldr-hover {
  background: rgba(246, 196, 83, 0.28);
}
.chip-tldr-text {
  color: #f6c453 !important;
  font-weight: 700 !important;
}

/* ─── 列表 ─── */
.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  padding: 16rpx 24rpx;
}

.loading-more,
.list-end {
  padding: 24rpx 0;
  text-align: center;
}

.loading-more-text,
.list-end-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}

/* ─── state blocks ─── */
.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
  padding: 120rpx 32rpx;
}

.state-emoji {
  font-size: 80rpx;
  line-height: 1;
}

.state-text {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.state-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}
</style>
