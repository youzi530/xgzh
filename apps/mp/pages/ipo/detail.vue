<script setup lang="ts">
/**
 * 新股详情页 (FE-005, 依赖 BE-009 / BE-010).
 *
 * 结构:
 * 1. 顶部 IPO 风险提示 banner (固定可见, spec/06 §法律隔离硬要求)
 * 2. Header 区: 名称 + code + status 色块 + 关注按钮
 * 3. 基本信息卡 (info-grid 6 格)
 * 4. 4-tab 切换:
 *    - 基本面: 财务摘要 (financial_summary 字段渲染)
 *    - 保荐人: sponsors / underwriters / prospectus_url
 *    - 亮点: highlights bullet list
 *    - 风险: risks bullet list
 * 5. AI 诊断 CTA (固定显示, "VIP 限免"占位角标)
 * 6. footer: 数据来源 + 更新时间 + 免责
 *
 * 数据流:
 * - 详情数据走 BE-009 (30min 缓存); 关注状态走 ``useFavoritesStore`` (登录后首次进
 *   触发 ``loadOnce``, 后续乐观更新; FE-006 自选 Tab 复用同一份数据)
 * - 未登录访问详情仍可看, AI 诊断仍可点 (匿名也允许调用 agent)
 * - 关注按钮在未登录时弹 modal 引导, 不阻断浏览
 */

import { onLoad } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  fetchIPODetail,
  type IPODetail,
  statusLabel,
  statusPalette,
} from '@/api/ipo'
import FavoriteButton from '@/components/FavoriteButton.vue'
import { useAuthStore } from '@/stores/auth'
import { useFavoritesStore } from '@/stores/favorites'

type Tab = 'fundamental' | 'sponsor' | 'highlights' | 'risks'

const TABS: { key: Tab; label: string }[] = [
  { key: 'fundamental', label: '基本面' },
  { key: 'sponsor', label: '保荐承销' },
  { key: 'highlights', label: '投资亮点' },
  { key: 'risks', label: '主要风险' },
]

const code = ref('')
const name = ref('')
const item = ref<IPODetail | null>(null)
const loading = ref(false)
const error = ref('')
const errorCode = ref('')
const activeTab = ref<Tab>('fundamental')

const authStore = useAuthStore()
const { loggedIn } = storeToRefs(authStore)
const favStore = useFavoritesStore()

const palette = computed(() => (item.value ? statusPalette(item.value.status) : null))
const statusText = computed(() => (item.value ? statusLabel(item.value.status) : ''))

/**
 * 财务摘要 ``Record<string, unknown>`` → ``[{label, value}]`` 列表.
 * 后端字段名遵循 snake_case (revenue / net_profit / gross_margin / ...);
 * 这里给已知字段 i18n 文案, 未知字段降级到原 key (容错让后端可加新字段不需要改前端)。
 */
const financialEntries = computed<{ label: string; value: string }[]>(() => {
  const fs = item.value?.financial_summary
  if (!fs || typeof fs !== 'object') return []
  const labelMap: Record<string, string> = {
    revenue: '营业收入',
    net_profit: '净利润',
    gross_margin: '毛利率',
    roe: '净资产收益率',
    debt_ratio: '资产负债率',
    eps: '每股收益',
    operating_cash_flow: '经营性现金流',
    period: '报告期',
    currency: '币种',
  }
  return Object.entries(fs)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => ({
      label: labelMap[k] ?? k,
      value: typeof v === 'number' ? formatNumber(k, v) : String(v),
    }))
})

const dataSourceText = computed(() => {
  if (!item.value) return ''
  const parts: string[] = []
  if (item.value.data_source) parts.push(`数据来源: ${item.value.data_source}`)
  if (item.value.updated_at) {
    const m = /(\d{4}-\d{2}-\d{2}T\d{2}:\d{2})/.exec(item.value.updated_at)
    if (m) parts.push(`更新于: ${m[1].replace('T', ' ')}`)
  }
  return parts.join(' · ')
})

function formatNumber(key: string, v: number): string {
  // 比率字段按百分号格式化
  if (['gross_margin', 'roe', 'debt_ratio'].includes(key)) {
    return `${(v * 100).toFixed(2)}%`
  }
  // 大数字 (营收 / 净利润等) 走万 / 亿单位; 后端给的是基础货币单位
  if (Math.abs(v) >= 1e8) return `${(v / 1e8).toFixed(2)} 亿`
  if (Math.abs(v) >= 1e4) return `${(v / 1e4).toFixed(2)} 万`
  return v.toString()
}

onLoad((query) => {
  code.value = decodeURIComponent((query?.code as string) ?? '')
  name.value = decodeURIComponent((query?.name as string) ?? '')
  if (code.value) load()
  // 登录态下预热自选数据, 关注按钮立即知道 favored 状态
  if (loggedIn.value) {
    favStore.loadOnce().catch(() => {
      // 预热失败不阻塞详情页; FavoriteButton 点击时还会再调 API
    })
  }
})

async function load() {
  loading.value = true
  error.value = ''
  errorCode.value = ''
  try {
    item.value = await fetchIPODetail(code.value)
  } catch (e) {
    const msg = (e as Error).message
    if (msg.includes('404')) {
      errorCode.value = 'ipo_not_found'
      error.value = '该新股暂未在数据源命中, 仍可使用 AI 诊断进行通用分析'
    } else {
      error.value = msg
    }
  } finally {
    loading.value = false
  }
}

function gotoAgent() {
  uni.navigateTo({
    url: `/pages/ipo/agent?code=${encodeURIComponent(code.value)}&name=${encodeURIComponent(name.value || item.value?.name || '')}`,
  })
}

function openProspectus() {
  const url = item.value?.prospectus_url
  if (!url) return
  // #ifdef MP-WEIXIN
  uni.showModal({
    title: '即将打开外部链接',
    content: '小程序内不便阅读 PDF, 复制链接后用浏览器打开?',
    confirmText: '复制链接',
    success: (r) => {
      if (r.confirm) {
        uni.setClipboardData({ data: url })
      }
    },
  })
  // #endif
  // #ifndef MP-WEIXIN
  // H5 / App 直接 webview
  // @ts-expect-error uni-app types 在某些版本里没暴露 navigateTo 的 webview 用法
  uni.navigateTo({ url: `/hybrid/html/web-view?url=${encodeURIComponent(url)}` })
  // #endif
}
</script>

<template>
  <view class="page">
    <view class="risk-banner">
      <text class="risk-banner-text">⚠️ IPO 投资有重大风险, 本页内容仅供信息聚合参考, 不构成投资建议</text>
    </view>

    <view class="header">
      <view class="header-main">
        <view class="header-title-row">
          <text class="title">{{ item?.name || name || code }}</text>
          <view
            v-if="item && palette"
            class="status-badge"
            :style="{ background: palette.bg, color: palette.fg, border: `1rpx solid ${palette.border}` }"
          >
            <text>{{ statusText }}</text>
          </view>
        </view>
        <text class="code">{{ code }}</text>
      </view>
      <FavoriteButton v-if="code" :code="code" />
    </view>

    <view v-if="loading && !item" class="state">加载中…</view>
    <view v-else-if="error && !item" class="state state-warn">{{ error }}</view>

    <template v-if="item">
      <view class="info-grid">
        <view class="info-cell">
          <text class="info-label">市场</text>
          <text class="info-value">{{ item.market }}</text>
        </view>
        <view class="info-cell">
          <text class="info-label">行业</text>
          <text class="info-value">{{ item.industry || '--' }}</text>
        </view>
        <view class="info-cell">
          <text class="info-label">发行价</text>
          <text class="info-value">
            {{ item.issue_price != null ? `${item.issue_currency ?? ''} ${Number(item.issue_price).toFixed(2)}` : '--' }}
          </text>
        </view>
        <view class="info-cell">
          <text class="info-label">PE</text>
          <text class="info-value">{{ item.pe_ratio != null ? Number(item.pe_ratio).toFixed(2) : '--' }}</text>
        </view>
        <view class="info-cell">
          <text class="info-label">上市日期</text>
          <text class="info-value">{{ item.listing_date || '--' }}</text>
        </view>
        <view class="info-cell">
          <text class="info-label">中签率</text>
          <text class="info-value">
            {{ item.one_lot_winning_rate != null ? `${(Number(item.one_lot_winning_rate) * 100).toFixed(2)}%` : '--' }}
          </text>
        </view>
      </view>

      <scroll-view scroll-x class="tabs" :show-scrollbar="false">
        <view
          v-for="t in TABS"
          :key="t.key"
          :class="['tab', activeTab === t.key && 'tab-active']"
          @tap="activeTab = t.key"
        >
          <text>{{ t.label }}</text>
        </view>
      </scroll-view>

      <view class="tab-body">
        <!-- 基本面 / 财务摘要 -->
        <view v-if="activeTab === 'fundamental'">
          <view v-if="financialEntries.length === 0" class="empty">
            <text>财务摘要暂未补齐 (后续接 AKShare / 招股书 RAG)</text>
          </view>
          <view v-else class="kv-grid">
            <view v-for="row in financialEntries" :key="row.label" class="kv-row">
              <text class="kv-label">{{ row.label }}</text>
              <text class="kv-value">{{ row.value }}</text>
            </view>
          </view>
        </view>

        <!-- 保荐承销 -->
        <view v-else-if="activeTab === 'sponsor'">
          <view v-if="!item.sponsors?.length && !item.underwriters?.length && !item.prospectus_url" class="empty">
            <text>保荐 / 承销信息暂未补齐</text>
          </view>
          <template v-else>
            <view v-if="item.sponsors?.length" class="sub-section">
              <text class="sub-title">保荐人 / 主承销商</text>
              <view class="chip-list">
                <text v-for="s in item.sponsors" :key="s" class="chip">{{ s }}</text>
              </view>
            </view>
            <view v-if="item.underwriters?.length" class="sub-section">
              <text class="sub-title">联席承销商</text>
              <view class="chip-list">
                <text v-for="u in item.underwriters" :key="u" class="chip">{{ u }}</text>
              </view>
            </view>
            <view v-if="item.prospectus_url" class="sub-section">
              <text class="sub-title">招股书</text>
              <view class="link-row" @tap="openProspectus">
                <text class="link-text">{{ item.prospectus_url }}</text>
                <text class="link-arrow">›</text>
              </view>
            </view>
          </template>
        </view>

        <!-- 亮点 -->
        <view v-else-if="activeTab === 'highlights'">
          <view v-if="!item.highlights?.length" class="empty">
            <text>亮点要点暂未补齐 (后续接 BE-018 招股书 RAG)</text>
          </view>
          <view v-else class="bullet-list">
            <view v-for="(h, idx) in item.highlights" :key="idx" class="bullet-item">
              <text class="bullet-dot bullet-dot-pos">+</text>
              <text class="bullet-text">{{ h }}</text>
            </view>
          </view>
        </view>

        <!-- 风险 -->
        <view v-else-if="activeTab === 'risks'">
          <view v-if="!item.risks?.length" class="empty">
            <text>风险要点暂未补齐 (后续接 BE-018 招股书 RAG)</text>
          </view>
          <view v-else class="bullet-list">
            <view v-for="(r, idx) in item.risks" :key="idx" class="bullet-item">
              <text class="bullet-dot bullet-dot-neg">!</text>
              <text class="bullet-text">{{ r }}</text>
            </view>
          </view>
        </view>
      </view>
    </template>

    <view class="cta-block" @tap="gotoAgent">
      <view class="cta-tag">VIP 限免</view>
      <view class="cta-title">⚡ AI 一键诊断</view>
      <view class="cta-desc">基于 DeepSeek-V3, 输出基本面 / 风险 / 多空观点</view>
      <view class="cta-arrow">→</view>
    </view>

    <view v-if="dataSourceText" class="datasource">{{ dataSourceText }}</view>

    <view class="disclaimer">
      本内容仅供参考, 不构成投资建议。最终以官方招股书 / 公告为准, 投资有风险, 入市需谨慎。
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.risk-banner {
  background: rgba(239, 68, 68, 0.08);
  border: 1rpx solid rgba(239, 68, 68, 0.3);
  border-radius: 12rpx;
  padding: 16rpx 24rpx;
}
.risk-banner-text {
  font-size: 22rpx;
  color: #ef4444;
  line-height: 1.5;
}

.header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16rpx;
}
.header-main {
  flex: 1;
  min-width: 0;
}
.header-title-row {
  display: flex;
  align-items: center;
  gap: 16rpx;
}
.title {
  font-size: 40rpx;
  font-weight: 700;
  color: var(--color-text);
  flex: 1;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.status-badge {
  flex-shrink: 0;
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  font-size: 22rpx;
  font-weight: 600;
  letter-spacing: 1rpx;
}
.code {
  display: block;
  margin-top: 4rpx;
  color: var(--color-text-muted);
  font-size: 24rpx;
}

.state {
  padding: 60rpx 0;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 26rpx;
}
.state-warn {
  color: var(--color-accent);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
}
.info-cell {
  padding: 20rpx;
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 12rpx;
}
.info-label {
  display: block;
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.info-value {
  display: block;
  margin-top: 8rpx;
  font-size: 30rpx;
  color: var(--color-text);
  font-weight: 600;
}

.tabs {
  white-space: nowrap;
  border-bottom: 1rpx solid var(--color-border);
}
.tab {
  display: inline-block;
  padding: 16rpx 28rpx;
  font-size: 26rpx;
  color: var(--color-text-muted);
  position: relative;
}
.tab-active {
  color: var(--color-text);
  font-weight: 600;
  &::after {
    content: '';
    position: absolute;
    left: 28rpx;
    right: 28rpx;
    bottom: 0;
    height: 4rpx;
    border-radius: 2rpx;
    background: var(--color-primary);
  }
}

.tab-body {
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 16rpx;
  padding: 24rpx;
  min-height: 200rpx;
}
.empty {
  text-align: center;
  padding: 32rpx 0;
  color: var(--color-text-muted);
  font-size: 24rpx;
}

.kv-grid {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.kv-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 12rpx 0;
  border-bottom: 1rpx dashed var(--color-border);

  &:last-child {
    border-bottom: none;
  }
}
.kv-label {
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.kv-value {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text);
}

.sub-section {
  margin-bottom: 20rpx;
  &:last-child {
    margin-bottom: 0;
  }
}
.sub-title {
  display: block;
  font-size: 24rpx;
  color: var(--color-text-muted);
  margin-bottom: 12rpx;
}
.chip-list {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
}
.chip {
  padding: 8rpx 20rpx;
  background: rgba(79, 139, 255, 0.1);
  color: var(--color-primary);
  border-radius: 999rpx;
  font-size: 24rpx;
  border: 1rpx solid rgba(79, 139, 255, 0.25);
}
.link-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 16rpx 0;
}
.link-text {
  flex: 1;
  font-size: 24rpx;
  color: var(--color-primary);
  word-break: break-all;
}
.link-arrow {
  font-size: 32rpx;
  color: var(--color-text-muted);
}

.bullet-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.bullet-item {
  display: flex;
  align-items: flex-start;
  gap: 16rpx;
}
.bullet-dot {
  flex-shrink: 0;
  width: 36rpx;
  height: 36rpx;
  border-radius: 50%;
  text-align: center;
  line-height: 36rpx;
  font-size: 24rpx;
  font-weight: 700;
  color: #fff;
}
.bullet-dot-pos {
  background: #22c55e;
}
.bullet-dot-neg {
  background: #ef4444;
}
.bullet-text {
  flex: 1;
  font-size: 26rpx;
  color: var(--color-text);
  line-height: 1.5;
}

.cta-block {
  position: relative;
  padding: 32rpx;
  border-radius: 16rpx;
  background: linear-gradient(135deg, #4f8bff, #7c3aed);
  color: #fff;
}
.cta-tag {
  position: absolute;
  top: 20rpx;
  right: 60rpx;
  padding: 4rpx 16rpx;
  background: rgba(255, 255, 255, 0.18);
  border-radius: 999rpx;
  font-size: 20rpx;
  font-weight: 600;
  letter-spacing: 1rpx;
}
.cta-title {
  font-size: 34rpx;
  font-weight: 700;
}
.cta-desc {
  margin-top: 8rpx;
  font-size: 24rpx;
  opacity: 0.9;
}
.cta-arrow {
  position: absolute;
  top: 50%;
  right: 32rpx;
  transform: translateY(-50%);
  font-size: 40rpx;
}

.datasource {
  font-size: 22rpx;
  color: var(--color-text-muted);
  text-align: center;
}
.disclaimer {
  font-size: 22rpx;
  color: var(--color-text-muted);
  line-height: 1.6;
  text-align: center;
  opacity: 0.85;
}
</style>
