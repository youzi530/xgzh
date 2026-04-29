<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 券商详情页 (FE-S3-003).
 *
 * 路由: ``/pages/broker/detail?slug=XXX``
 *
 * 模块:
 * 1. **顶部 hero**: logo + 名 (中英) + 市场 chip 列表 + 牌照 chip 列表
 * 2. **推广卡 (有 active promotion 时)**: 标题 + 描述 + 倒计时 + 邀请码 (可复制)
 * 3. **费率明细表**: 按 fees 字段 key-value 渲染所有键 (港股佣金 / 平台费 / 融资利率 等)
 * 4. **平台特性**: 按 features 字段 key-value 渲染 (新股申购 / 暗池 / 中文服务 等)
 * 5. **计算示例**: 简化 — 港股买 100 股 ¥X 估算佣金 (帮用户感性理解费率)
 * 6. **底部固定 CTA**: "立即开户" (utm_campaign='detail_cta', 与列表页区分归因)
 * 7. **合规底部**: spec/03 §模块四 风险揭示
 *
 * 设计取舍:
 *
 * - **fees / features key 中英映射用 dict + fallback**: BE seed 各券商可能加新 key
 *   (例如 ``us_commission_per_share``), 前端 dict 兜底 = 直接显 raw key 让运营/QA
 *   能看出来"这是新加的字段, FE 没翻译". 不抛 i18n missing key 报错
 *
 * - **不渲染所有 fees 的所有 key**: 各券商 fees JSONB 结构不一, 但有些 key (例如
 *   ``platform_fee_currency``) 是另一字段的辅助标识不该独立显示. 走白名单 + 显式
 *   配置渲染顺序, 而非"全展示"
 *
 * - **计算示例固定"100 股港股 / 单价 100 港币"场景**: 用户最直观的对比基准 (1 万港币
 *   流水); 不做交互式计算器 (那是 P1 工具, MVP 简单示例足够)
 *
 * - **utm_campaign='detail_cta' vs 列表页 'compare_table'**: BE-S3-008 conversion_events
 *   按 utm_campaign 维度 GROUP BY, 让运营漏斗能看出"用户是先看详情才开户" vs
 *   "看列表直接开户" 的转化路径差异. 数据指导 UX 优化
 *
 * - **detail page 也用 `getDeviceId()`**: 与列表页同 device id, BE 1h 防刷在
 *   (broker_id, actor_key, utm_campaign) 三元组维度 — utm_campaign 不同时各落 1 行,
 *   即"列表 click → 1h 内进详情再 click" 的路径会落 2 行 (compare_table + detail_cta)
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  buildRedirectUrl,
  fetchBrokerDetail,
  type BrokerPublic,
} from '@/api/broker'
import { getDeviceId } from '@/utils/device'
import { getNavParam } from '@/utils/navigate'

const broker = ref<BrokerPublic | null>(null)
const loading = ref<boolean>(true)
const error = ref<string>('')

/** logo 兜底首字符 */
const logoFallback = computed(() => {
  const name = broker.value?.name_zh || broker.value?.name_en || '?'
  return name.slice(0, 1)
})

/** 推广 / 费率 / 特性 字段映射 (key → 中文 label) */
const FEES_LABELS: Record<string, string> = {
  hk_commission_rate: '港股佣金率',
  hk_min_commission: '港股最低佣金 (港币)',
  us_commission_rate: '美股佣金率',
  us_commission_per_share: '美股每股 (美元)',
  us_min_commission: '美股最低佣金 (美元)',
  platform_fee: '平台费',
  platform_fee_currency: '平台费币种',
  margin_rate_hkd: '港币融资利率',
  margin_rate_usd: '美元融资利率',
  cancel_fee: '撤单费',
  custodian_fee: '存管费',
  ipo_subscription_fee: '打新认购费',
}

const FEATURES_LABELS: Record<string, string> = {
  ipo_subscription: '新股申购',
  dark_pool_trading: '暗池交易',
  margin_trading: '融资融券',
  options_trading: '期权交易',
  chinese_service: '中文客服',
  min_deposit_hkd: '入金门槛 (港币)',
  min_deposit_usd: '入金门槛 (美元)',
  paper_trading: '模拟交易',
  fractional_shares: '碎股交易',
}

/** 单元格值格式化: 数字 / 布尔 / 字符串 三类 */
function formatValue(v: unknown, key: string): string {
  if (v === null || v === undefined) return '—'
  if (typeof v === 'boolean') return v ? '✓ 支持' : '✗ 不支持'
  if (typeof v === 'number') {
    if (key.includes('rate')) return `${(v * 100).toFixed(3)}%`
    if (v === 0 && (key.includes('commission') || key.includes('fee'))) return '免'
    return String(v)
  }
  if (typeof v === 'string') return v
  return JSON.stringify(v)
}

/** 渲染顺序固定的 fees rows, 仅展示存在的 key */
const feesRows = computed(() => {
  const fees = broker.value?.fees || {}
  return Object.keys(FEES_LABELS)
    .filter((k) => k in fees)
    .map((k) => ({
      key: k,
      label: FEES_LABELS[k],
      value: formatValue(fees[k], k),
    }))
})

const featuresRows = computed(() => {
  const features = broker.value?.features || {}
  return Object.keys(FEATURES_LABELS)
    .filter((k) => k in features)
    .map((k) => ({
      key: k,
      label: FEATURES_LABELS[k],
      value: formatValue(features[k], k),
    }))
})

/** 推广倒计时 */
const promotionCountdown = computed(() => {
  const endAt = broker.value?.promotion?.end_at
  if (typeof endAt !== 'string') return ''
  const remainMs = new Date(endAt).getTime() - Date.now()
  if (Number.isNaN(remainMs)) return ''
  if (remainMs <= 0) return '活动已结束'
  const days = Math.floor(remainMs / 86_400_000)
  const hours = Math.floor((remainMs % 86_400_000) / 3_600_000)
  if (days >= 7) return `活动还剩 ${days} 天`
  if (days >= 1) return `活动还剩 ${days} 天 ${hours} 时`
  return `${hours} 小时内截止`
})

const promotionExpired = computed(() => {
  const endAt = broker.value?.promotion?.end_at
  if (typeof endAt !== 'string') return false
  return new Date(endAt).getTime() <= Date.now()
})

const promotionActive = computed(
  () => Boolean(broker.value?.promotion?.is_active) && !promotionExpired.value,
)

/**
 * 港股佣金 100 股 × 100 港币 = 1 万港币流水的估算.
 *
 * 计算: max(commission_rate × 10000, min_commission) + platform_fee
 * 让用户感性看到 "1 万港币流水我大约花多少钱"
 */
const exampleCalculation = computed(() => {
  const fees = broker.value?.fees || {}
  const rate = fees['hk_commission_rate']
  const minCom = fees['hk_min_commission']
  const platform = fees['platform_fee']
  const platformCur = fees['platform_fee_currency']

  if (typeof rate !== 'number') return null

  const flowAmount = 10_000 // 1 万港币
  const rateBased = flowAmount * rate
  const commission = typeof minCom === 'number' ? Math.max(rateBased, minCom) : rateBased
  const platformValid = typeof platform === 'number' && platform > 0
  const total = commission + (platformValid ? platform : 0)

  return {
    flow: `${flowAmount.toLocaleString()} 港币`,
    rate: `${(rate * 100).toFixed(3)}%`,
    commission: commission === 0 ? '免' : `${commission.toFixed(2)} 港币`,
    platformText: platformValid
      ? `平台费 ${platform} ${typeof platformCur === 'string' ? platformCur : ''}`
      : '免平台费',
    total: total === 0 ? '免' : `${total.toFixed(2)} 港币`,
  }
})

const promotionTitle = computed(() => {
  const t = broker.value?.promotion?.title
  return typeof t === 'string' ? t : ''
})

const promotionDesc = computed(() => {
  const d = broker.value?.promotion?.description
  return typeof d === 'string' ? d : ''
})

const promotionInviteCode = computed(() => {
  const c = broker.value?.promotion?.invite_code
  return typeof c === 'string' ? c : ''
})

async function load(slug: string) {
  loading.value = true
  error.value = ''
  try {
    broker.value = await fetchBrokerDetail(slug)
  } catch (e) {
    const err = e as { statusCode?: number }
    if (err?.statusCode === 404) {
      error.value = '该券商不存在或已下架'
    } else {
      error.value = '加载失败, 请检查网络后重试'
    }
    console.warn('[broker-detail] fetch failed', e)
  } finally {
    loading.value = false
  }
}

function copyInviteCode() {
  if (!promotionInviteCode.value) return
  uni.setClipboardData({
    data: promotionInviteCode.value,
    success: () => uni.showToast({ title: '邀请码已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

/**
 * 立即开户 — 跨端跳转, 与 list 页 ``openRedirect`` 同款逻辑.
 *
 * utm_campaign='detail_cta' 区分于列表页的 'compare_table'; BE-S3-008 conversion_events
 * GROUP BY 时能区分"列表直接 click" vs "进详情后 click" 两条转化路径.
 */
function openRedirect() {
  if (!broker.value) return
  const url = buildRedirectUrl(broker.value.slug, {
    utm_campaign: 'detail_cta',
    utm_medium: 'detail-page',
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
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

function gotoList() {
  uni.navigateBack({ fail: () => uni.reLaunch({ url: '/pages/broker/index' }) })
}

onLoad((options) => {
  // QA-S5-001 BC-4: getNavParam 统一跨端 decode (slug 是 ASCII safe, 但保持 helper 一致)
  const slug = getNavParam(options, 'slug')
  if (!slug) {
    error.value = '缺少 slug 参数'
    loading.value = false
    return
  }
  void load(slug)
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── loading ─── -->
    <view v-if="loading" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <!-- ─── error ─── -->
    <view v-else-if="error" class="state-block">
      <text class="state-emoji">😕</text>
      <text class="state-text">{{ error }}</text>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="gotoList">
        <text class="state-cta-text">回列表</text>
      </view>
    </view>

    <template v-else-if="broker">
      <view class="content">
        <!-- ─── hero ─── -->
        <view class="hero">
          <view class="hero-top">
            <image
              v-if="broker.logo_url"
              class="hero-logo"
              :src="broker.logo_url"
              mode="aspectFit"
            />
            <view v-else class="hero-logo hero-logo-fallback">
              <text class="hero-logo-text">{{ logoFallback }}</text>
            </view>
            <view class="hero-name">
              <text class="hero-name-zh">{{ broker.name_zh }}</text>
              <text v-if="broker.name_en" class="hero-name-en">{{ broker.name_en }}</text>
            </view>
          </view>

          <view v-if="broker.market_support.length > 0" class="hero-section">
            <text class="hero-section-label">支持市场</text>
            <view class="hero-chips">
              <view v-for="m in broker.market_support" :key="m" class="hero-chip hero-chip-market">
                <text class="hero-chip-text">{{ m }}</text>
              </view>
            </view>
          </view>

          <view v-if="broker.licenses.length > 0" class="hero-section">
            <text class="hero-section-label">监管牌照</text>
            <view class="hero-chips">
              <view v-for="l in broker.licenses" :key="l" class="hero-chip hero-chip-license">
                <text class="hero-chip-text">{{ l }}</text>
              </view>
            </view>
          </view>
        </view>

        <!-- ─── 推广卡 ─── -->
        <view v-if="promotionActive && promotionTitle" class="promo-card">
          <view class="promo-head">
            <text class="promo-emoji">🎁</text>
            <text class="promo-title">{{ promotionTitle }}</text>
          </view>
          <text v-if="promotionDesc" class="promo-desc">{{ promotionDesc }}</text>
          <view class="promo-meta-row">
            <text v-if="promotionCountdown" class="promo-countdown">{{ promotionCountdown }}</text>
            <view
              v-if="promotionInviteCode"
              class="promo-invite"
              hover-class="promo-invite-hover"
              :hover-stay-time="80"
              @tap="copyInviteCode"
            >
              <text class="promo-invite-label">邀请码</text>
              <text class="promo-invite-code">{{ promotionInviteCode }}</text>
              <text class="promo-invite-icon">📋</text>
            </view>
          </view>
        </view>

        <!-- ─── 费率明细 ─── -->
        <view v-if="feesRows.length > 0" class="info-card">
          <text class="info-card-title">费率明细</text>
          <view class="info-rows">
            <view v-for="r in feesRows" :key="r.key" class="info-row">
              <text class="info-row-label">{{ r.label }}</text>
              <text class="info-row-value">{{ r.value }}</text>
            </view>
          </view>
        </view>

        <!-- ─── 计算示例 ─── -->
        <view v-if="exampleCalculation" class="example-card">
          <text class="example-title">📊 港股 1 万港币交易费用估算</text>
          <view class="example-rows">
            <view class="example-row">
              <text class="example-row-label">交易额</text>
              <text class="example-row-value">{{ exampleCalculation.flow }}</text>
            </view>
            <view class="example-row">
              <text class="example-row-label">佣金 (按 {{ exampleCalculation.rate }})</text>
              <text class="example-row-value">{{ exampleCalculation.commission }}</text>
            </view>
            <view class="example-row">
              <text class="example-row-label">平台费</text>
              <text class="example-row-value">{{ exampleCalculation.platformText }}</text>
            </view>
            <view class="example-row example-row-total">
              <text class="example-row-label">合计</text>
              <text class="example-row-value example-row-value-total">{{ exampleCalculation.total }}</text>
            </view>
          </view>
          <text class="example-note">仅含基础佣金 + 平台费, 不含交易所费用 / 印花税 / 转手纸费 / 政府征费等</text>
        </view>

        <!-- ─── 平台特性 ─── -->
        <view v-if="featuresRows.length > 0" class="info-card">
          <text class="info-card-title">平台特性</text>
          <view class="info-rows">
            <view v-for="r in featuresRows" :key="r.key" class="info-row">
              <text class="info-row-label">{{ r.label }}</text>
              <text class="info-row-value">{{ r.value }}</text>
            </view>
          </view>
        </view>

        <!-- ─── 合规底部 ─── -->
        <view class="footer">
          <text class="footer-text">
            数据由 XGZH 收集整理, 可能存在滞后, 请以券商官网最终费率为准. XGZH 仅作信息聚合, 不承担投资责任.
          </text>
        </view>
      </view>

      <view class="bottom-spacer" />

      <!-- ─── 底部固定 CTA ─── -->
      <view class="cta-bar">
        <view
          class="cta-btn cta-btn-primary"
          hover-class="cta-btn-primary-hover"
          :hover-stay-time="80"
          @tap="openRedirect"
        >
          <text class="cta-btn-text">🔗 立即开户</text>
        </view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
}

.content {
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

/* ─── hero ─── */
.hero {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

.hero-top {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 20rpx;
}

.hero-logo {
  width: 96rpx;
  height: 96rpx;
  border-radius: 20rpx;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.06);
}

.hero-logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
}

.hero-logo-text {
  font-size: 38rpx;
  font-weight: 700;
  color: #fff;
}

.hero-name {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
}

.hero-name-zh {
  font-size: 36rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-name-en {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hero-section {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.hero-section-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-weight: 600;
}

.hero-chips {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8rpx;
}

.hero-chip {
  padding: 6rpx 14rpx;
  border-radius: 6rpx;
  border: 1rpx solid;
}

.hero-chip-market {
  background: rgba(79, 139, 255, 0.12);
  border-color: rgba(79, 139, 255, 0.3);
}

.hero-chip-license {
  background: rgba(34, 197, 94, 0.08);
  border-color: rgba(34, 197, 94, 0.32);
}

.hero-chip-market .hero-chip-text {
  font-size: 22rpx;
  color: #4f8bff;
  font-weight: 700;
}

.hero-chip-license .hero-chip-text {
  font-size: 20rpx;
  color: #22c55e;
  font-weight: 600;
}

/* ─── promo card ─── */
.promo-card {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.08), rgba(246, 196, 83, 0.06));
  border: 1rpx solid rgba(239, 68, 68, 0.32);
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.promo-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
}

.promo-emoji {
  font-size: 28rpx;
}

.promo-title {
  font-size: 28rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
}

.promo-desc {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  opacity: 0.85;
  line-height: 1.6;
}

.promo-meta-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  gap: 16rpx;
  flex-wrap: wrap;
}

.promo-countdown {
  font-size: 22rpx;
  color: #ef4444;
  font-weight: 700;
}

.promo-invite {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  padding: 8rpx 16rpx;
  border-radius: 8rpx;
  background: rgba(255, 255, 255, 0.05);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

.promo-invite-hover {
  background: rgba(255, 255, 255, 0.12);
}

.promo-invite-label {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}

.promo-invite-code {
  font-size: 22rpx;
  color: #f6c453;
  font-weight: 700;
}

.promo-invite-icon {
  font-size: 22rpx;
}

/* ─── info card (fees / features) ─── */
.info-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.info-card-title {
  font-size: 28rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
}

.info-rows {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.info-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  padding: 16rpx 0;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.04);
}

.info-row:last-child {
  border-bottom: none;
}

.info-row-label {
  flex: 1;
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.info-row-value {
  font-size: 24rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  text-align: right;
}

/* ─── example calculation ─── */
.example-card {
  background: linear-gradient(135deg, rgba(79, 139, 255, 0.06), rgba(110, 61, 240, 0.04));
  border: 1rpx solid rgba(79, 139, 255, 0.28);
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.example-title {
  font-size: 26rpx;
  font-weight: 700;
  color: #4f8bff;
}

.example-rows {
  display: flex;
  flex-direction: column;
  gap: 0;
}

.example-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  padding: 12rpx 0;
}

.example-row-total {
  border-top: 1rpx solid rgba(255, 255, 255, 0.08);
  padding-top: 16rpx;
  margin-top: 4rpx;
}

.example-row-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.example-row-value {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  font-weight: 600;
}

.example-row-value-total {
  font-size: 28rpx;
  color: #f6c453;
  font-weight: 800;
}

.example-note {
  margin-top: 8rpx;
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  line-height: 1.55;
}

/* ─── footer ─── */
.footer {
  padding-top: 8rpx;
}

.footer-text {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  line-height: 1.6;
}

/* ─── states ─── */
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
}

.state-text {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.state-cta {
  margin-top: 24rpx;
  padding: 22rpx 64rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.18);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
}

.state-cta-hover {
  background: rgba(79, 139, 255, 0.32);
}

.state-cta-text {
  font-size: 26rpx;
  font-weight: 700;
  color: #4f8bff;
}

/* ─── bottom CTA ─── */
.bottom-spacer {
  height: 180rpx;
}

.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: row;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.95);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
}

.cta-btn {
  flex: 1;
  padding: 24rpx 0;
  text-align: center;
  border-radius: 999rpx;
}

.cta-btn-primary {
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.18);
}

.cta-btn-primary-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}

.cta-btn-text {
  font-size: 30rpx;
  font-weight: 800;
  color: #ffffff;
}
</style>
