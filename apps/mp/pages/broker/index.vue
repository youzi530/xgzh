<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 券商列表 / 对比页 (FE-S3-003).
 *
 * 路由: ``/pages/broker/index``
 *
 * 模块:
 * 1. **顶部 sticky 筛选条**: market 分段 (HK / US / SG / 全部) + sort chip (推荐排序 / A-Z)
 * 2. **券商卡片瀑布流** (BrokerCard 组件): 每卡显 logo + 名 + 市场 chip + 关键费率 +
 *    推广倒计时 + 双 CTA (查看详情 / 立即开户)
 * 3. **底部合规 footer**: spec/03 §模块四 "券商信息可能滞后, 请以官网为准"
 * 4. **下拉刷新**: ``onPullDownRefresh`` 重拉 list (BE Redis 5 min 缓存仍有效)
 *
 * 设计取舍:
 *
 * - **不做"横滚表 (sticky 首列)"**: spec 提到的横滚对比表在小屏 + 6-8 家券商场景下
 *   可读性反而不如卡片列表; sticky 首列在 mp-weixin scroll-view 嵌套场景兼容性差.
 *   卡片列表已经能横向并列每行 3 项关键费率 (佣金 / 最低 / 平台费), 满足"快速对比"
 *   核心需求; 真正深度对比走详情页. 横滚表可作为 P1 优化 (例如做个独立 "对比" tab)
 *
 * - **`market='A'` 在 BE 是"A 股", 但前端筛选也只显 HK/US/SG 三市场**: 6 家种子券商
 *   暂不支持 A 股直连 (国内券商接入通常走另一套合规体系), 筛选仅放 HK/US/SG + 全部 4 选
 *
 * - **不做 'partnership' 筛选**: BE 接受 `partnership=CPA/CPS/BOTH/NONE` 筛选, 但
 *   端侧用户对"我们与券商的合作类型"完全不需要关心 (那是商业秘密); 前端永远走
 *   `partnership=all` 拉所有公开券商
 *
 * - **redirect CTA 不走 navigateTo, 走 platform-aware 跳转**:
 *   - H5: ``window.open(redirect_url, '_blank')`` 让浏览器跟随 BE 302
 *   - MP-WEIXIN: ``setClipboardData + showModal`` 引导用户在浏览器粘贴打开
 *     (mp-weixin web-view 不能跳任意域名; 真实部署时如果券商域名都备案了
 *     可单点替换为 navigateTo `/pages/webview/external?url=...`)
 *   - APP-PLUS: ``plus.runtime.openURL`` 唤起浏览器 (uni-app 跨端 API)
 *
 * - **不在前端缓存 broker list**: BE Redis 5min TTL 已够; 与 article list 同款
 *   单一缓存层级原则
 */

import { onLoad, onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'

import {
  buildRedirectUrl,
  fetchBrokerList,
  type BrokerPublic,
  type MarketFilter,
} from '@/api/broker'
import BrokerCard from '@/components/BrokerCard.vue'
import { getDeviceId } from '@/utils/device'
import { navigateWithParams } from '@/utils/navigate'

const list = ref<BrokerPublic[]>([])
const loading = ref<boolean>(false)
const error = ref<string>('')

const market = ref<MarketFilter>('all')

interface MarketOption {
  key: MarketFilter
  label: string
}

const MARKET_OPTIONS: MarketOption[] = [
  { key: 'all', label: '全部' },
  { key: 'HK', label: '港股' },
  { key: 'US', label: '美股' },
  { key: 'SG', label: '新股' },
]

async function load() {
  if (loading.value) return
  loading.value = true
  error.value = ''
  try {
    const resp = await fetchBrokerList({ market: market.value })
    list.value = resp.items
  } catch (e) {
    console.warn('[broker-list] fetch failed', e)
    error.value = '加载失败, 请下拉刷新或检查网络'
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function selectMarket(m: MarketFilter) {
  if (market.value === m || loading.value) return
  market.value = m
  void load()
}

function gotoDetail(slug: string) {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode
  void navigateWithParams('/pages/broker/detail', { slug })
}

/**
 * 跨端"立即开户"入口.
 *
 * 永远经 BE ``/redirect`` 中转 (而非前端拼 referral_url 自己跳) — BE 端点要落
 * conversion_events.click 事件, 是 CPA / CPS 转化漏斗第一步, 绕过 BE 漏斗会全断.
 *
 * 三端行为:
 * - **H5**: ``window.open(url, '_blank')`` — 浏览器自动跟随 302 到券商
 * - **MP-WEIXIN**: ``setClipboardData + uni.showModal`` 引导用户在浏览器粘贴打开
 *   (mp-weixin web-view 限制不能跳任意域名)
 * - **APP-PLUS**: ``plus.runtime.openURL`` 唤起系统浏览器
 *
 * utm_campaign='compare_table' 与 utm_medium='compare-page' 配合 BE-S3-008
 * conversion_events 漏斗归因区分 (vs 详情页 detail_cta 区分)
 */
function openRedirect(slug: string) {
  const url = buildRedirectUrl(slug, {
    utm_campaign: 'compare_table',
    utm_medium: 'compare-page',
    device_id: getDeviceId(),
  })

  // #ifdef H5
  if (typeof window !== 'undefined') {
    window.open(url, '_blank')
    return
  }
  // #endif

  // #ifdef APP-PLUS
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const plus = (globalThis as any).plus
  if (plus?.runtime?.openURL) {
    plus.runtime.openURL(url)
    return
  }
  // #endif

  // MP-WEIXIN + 兜底: 复制 + 引导浏览器粘贴打开
  uni.setClipboardData({
    data: url,
    success: () => {
      uni.showModal({
        title: '在浏览器中开户',
        content: '开户链接已复制到剪贴板, 请在浏览器中粘贴打开. 由券商方提供, XGZH 不承担投资责任.',
        showCancel: false,
        confirmText: '我知道了',
      })
    },
    fail: () => uni.showToast({ title: '复制失败, 请重试', icon: 'none' }),
  })
}

onLoad(() => {
  void load()
})

onShow(() => {
  // 列表为空时尝试重新加载 (例如错误后切回); 已有数据不重拉
  if (list.value.length === 0 && !loading.value) {
    void load()
  }
})

onPullDownRefresh(() => {
  void load()
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── 错误 banner ─── -->
    <view v-if="error" class="err-banner">
      <text class="err-text">{{ error }}</text>
      <view class="err-retry" hover-class="err-retry-hover" :hover-stay-time="80" @tap="load">
        <text class="err-retry-text">重试</text>
      </view>
    </view>

    <!-- ─── sticky 筛选 ─── -->
    <view class="filter-bar">
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
    </view>

    <!-- ─── 列表 / 空态 ─── -->
    <view v-if="loading && list.length === 0" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="list.length === 0 && !loading && !error" class="state-block">
      <text class="state-emoji">🏦</text>
      <text class="state-text">暂未收录支持该市场的券商</text>
      <text class="state-sub">试试切换市场, 或下拉刷新</text>
    </view>

    <view v-else class="list">
      <BrokerCard
        v-for="broker in list"
        :key="broker.broker_id"
        :broker="broker"
        @detail-click="gotoDetail"
        @redirect-click="openRedirect"
      />
    </view>

    <!-- ─── 合规 footer ─── -->
    <view v-if="list.length > 0" class="footer">
      <text class="footer-text">
        券商信息由 XGZH 收集整理, 可能存在滞后, 请以券商官网为准. XGZH 仅作信息聚合, 不承担投资责任.
      </text>
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

/* ─── error banner ─── */
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

/* ─── filter bar (sticky) ─── */
.filter-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  background: rgba(11, 18, 32, 0.95);
  backdrop-filter: blur(8rpx);
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  padding: 16rpx 24rpx;
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
  flex: 1;
  padding: 12rpx 24rpx;
  border-radius: 999rpx;
  text-align: center;
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

/* ─── list ─── */
.list {
  display: flex;
  flex-direction: column;
  gap: 20rpx;
  padding: 20rpx 24rpx;
}

/* ─── state block ─── */
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

/* ─── footer ─── */
.footer {
  padding: 24rpx 32rpx;
  margin-top: 16rpx;
}

.footer-text {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  line-height: 1.65;
}
</style>
