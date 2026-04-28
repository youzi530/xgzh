<script setup lang="ts">
/**
 * 券商卡片 (FE-S3-003).
 *
 * 单卡片结构 (spec/03 §模块四):
 *
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ [logo]  富途牛牛 (Futu)         [HK · US · SG] (市场 chip)│
 *   │         佣金 0.03% · 最低 0     · 平台费 15 HKD          │
 *   │ ┌──────────────────────────────────────────────────────┐ │
 *   │ │ 🎁 [推广标题]                                         │ │
 *   │ │    [推广描述, 2 行截断]                               │ │
 *   │ │    活动至 2026-XX-XX 还剩 N 天                       │ │
 *   │ └──────────────────────────────────────────────────────┘ │
 *   │ [查看详情]  ⎮  [🔗 立即开户]                              │
 *   └──────────────────────────────────────────────────────────┘
 *
 * 设计取舍:
 *
 * - **关键费率单行展示**: 佣金率 / 最低佣金 / 平台费 三项铺一行 — 这是用户对比
 *   时最想看的核心数字; 牌照 / 特性等次要字段放详情页
 *
 * - **promotion.is_active=false 时整块隐藏**: 不显示"暂无活动" 占位 — 视觉留白
 *
 * - **倒计时显示**: end_at - now > 7 天 显 "还剩 X 天"; ≤ 7 天 显 "还剩 X 天 Y 时";
 *   过期 显灰色 "已结束"; 与 me/index.vue VIP 卡同款颗粒度策略
 *
 * - **市场 chip 限 3 项**: HK/A/US/SG 4 选 3 显示已经覆盖大多数; 4 个全显占两行
 *   破坏单卡 layout — 详情页才显示完整列表
 *
 * - **2 个 CTA 比 1 个强**: "查看详情"是探索路径 (低承诺), "立即开户"是转化路径
 *   (高承诺); 两个并存让用户分流, 不强迫"先看详情才能开户"
 */

import { computed } from 'vue'

import type { BrokerPublic } from '@/api/broker'

interface Props {
  broker: BrokerPublic
}

const props = defineProps<Props>()

const emit = defineEmits<{
  (e: 'detail-click', slug: string): void
  (e: 'redirect-click', slug: string): void
}>()

/** 香港佣金率展示 ("0.03%" / "—") */
const hkCommissionRate = computed(() => {
  const rate = props.broker.fees['hk_commission_rate']
  if (typeof rate === 'number') return `${(rate * 100).toFixed(3)}%`
  return '—'
})

/** 香港最低佣金 ("免" / "¥5" / "—") */
const hkMinCommission = computed(() => {
  const v = props.broker.fees['hk_min_commission']
  if (v === 0) return '免'
  if (typeof v === 'number') return `${v} 港币`
  return '—'
})

/** 平台费 ("15 HKD" / "—") */
const platformFee = computed(() => {
  const v = props.broker.fees['platform_fee']
  const cur = props.broker.fees['platform_fee_currency']
  if (v === 0) return '免'
  if (typeof v === 'number') {
    return typeof cur === 'string' ? `${v} ${cur}` : `${v}`
  }
  return '—'
})

/** 市场 chip 列表 (限 3 项) */
const marketChips = computed(() => props.broker.market_support.slice(0, 3))

/** logo 为空时的首字母 */
const logoFallback = computed(() => {
  const name = props.broker.name_zh || props.broker.name_en || '?'
  return name.slice(0, 1)
})

/** promotion 是否启用 (字段以 BE seed 约定为准: ``is_active`` boolean) */
const promotionActive = computed(() => Boolean(props.broker.promotion?.is_active))

const promotionTitle = computed(() => {
  const t = props.broker.promotion?.title
  return typeof t === 'string' ? t : ''
})

const promotionDesc = computed(() => {
  const d = props.broker.promotion?.description
  return typeof d === 'string' ? d : ''
})

/** 推广倒计时 ("还剩 30 天" / "还剩 5 天 12 时" / "已结束") */
const promotionCountdown = computed(() => {
  const endAt = props.broker.promotion?.end_at
  if (typeof endAt !== 'string') return ''
  // BE 序列化为 ISO date or ISO datetime; JS Date 都能解
  const remainMs = new Date(endAt).getTime() - Date.now()
  if (Number.isNaN(remainMs)) return ''
  if (remainMs <= 0) return '已结束'
  const days = Math.floor(remainMs / 86_400_000)
  const hours = Math.floor((remainMs % 86_400_000) / 3_600_000)
  if (days >= 7) return `还剩 ${days} 天`
  if (days >= 1) return `还剩 ${days} 天 ${hours} 时`
  return `${hours} 小时内截止`
})

const promotionExpired = computed(() => promotionCountdown.value === '已结束')
</script>

<template>
  <view class="bc-card">
    <!-- ─── 顶部: logo + 名 + 市场 chip ─── -->
    <view class="bc-head">
      <image
        v-if="broker.logo_url"
        class="bc-logo"
        :src="broker.logo_url"
        mode="aspectFit"
      />
      <view v-else class="bc-logo bc-logo-fallback">
        <text class="bc-logo-text">{{ logoFallback }}</text>
      </view>
      <view class="bc-name-meta">
        <text class="bc-name-zh">{{ broker.name_zh }}</text>
        <text v-if="broker.name_en" class="bc-name-en">{{ broker.name_en }}</text>
      </view>
      <view class="bc-markets">
        <view v-for="m in marketChips" :key="m" class="bc-market-chip">
          <text class="bc-market-text">{{ m }}</text>
        </view>
      </view>
    </view>

    <!-- ─── 关键费率单行 ─── -->
    <view class="bc-fees">
      <view class="bc-fee-item">
        <text class="bc-fee-value">{{ hkCommissionRate }}</text>
        <text class="bc-fee-label">港股佣金</text>
      </view>
      <view class="bc-fee-divider" />
      <view class="bc-fee-item">
        <text class="bc-fee-value">{{ hkMinCommission }}</text>
        <text class="bc-fee-label">最低佣金</text>
      </view>
      <view class="bc-fee-divider" />
      <view class="bc-fee-item">
        <text class="bc-fee-value">{{ platformFee }}</text>
        <text class="bc-fee-label">平台费</text>
      </view>
    </view>

    <!-- ─── 推广卡 ─── -->
    <view v-if="promotionActive && promotionTitle" :class="['bc-promo', promotionExpired && 'bc-promo-expired']">
      <view class="bc-promo-head">
        <text class="bc-promo-emoji">🎁</text>
        <text class="bc-promo-title">{{ promotionTitle }}</text>
      </view>
      <text v-if="promotionDesc" class="bc-promo-desc">{{ promotionDesc }}</text>
      <text v-if="promotionCountdown" :class="['bc-promo-countdown', promotionExpired && 'bc-promo-countdown-expired']">
        {{ promotionCountdown }}
      </text>
    </view>

    <!-- ─── 双 CTA ─── -->
    <view class="bc-cta-bar">
      <view
        class="bc-cta bc-cta-secondary"
        hover-class="bc-cta-secondary-hover"
        :hover-stay-time="80"
        @tap="emit('detail-click', broker.slug)"
      >
        <text class="bc-cta-text-ghost">查看详情</text>
      </view>
      <view
        class="bc-cta bc-cta-primary"
        hover-class="bc-cta-primary-hover"
        :hover-stay-time="80"
        @tap="emit('redirect-click', broker.slug)"
      >
        <text class="bc-cta-text">🔗 立即开户</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.bc-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
  padding: 28rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

/* ─── head ─── */
.bc-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
}

.bc-logo {
  width: 80rpx;
  height: 80rpx;
  border-radius: 16rpx;
  flex-shrink: 0;
  background: rgba(255, 255, 255, 0.06);
}

.bc-logo-fallback {
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
}

.bc-logo-text {
  font-size: 32rpx;
  font-weight: 700;
  color: #fff;
}

.bc-name-meta {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.bc-name-zh {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bc-name-en {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bc-markets {
  flex-shrink: 0;
  display: flex;
  flex-direction: row;
  gap: 6rpx;
}

.bc-market-chip {
  padding: 4rpx 12rpx;
  border-radius: 6rpx;
  background: rgba(79, 139, 255, 0.12);
  border: 1rpx solid rgba(79, 139, 255, 0.3);
}

.bc-market-text {
  font-size: 18rpx;
  color: #4f8bff;
  font-weight: 700;
}

/* ─── fees ─── */
.bc-fees {
  display: flex;
  flex-direction: row;
  align-items: center;
  background: rgba(255, 255, 255, 0.03);
  border-radius: 16rpx;
  padding: 16rpx 0;
}

.bc-fee-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6rpx;
}

.bc-fee-value {
  font-size: 28rpx;
  font-weight: 700;
  color: #f6c453;
}

.bc-fee-label {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}

.bc-fee-divider {
  width: 1rpx;
  height: 40rpx;
  background: rgba(255, 255, 255, 0.06);
}

/* ─── promotion ─── */
.bc-promo {
  background: linear-gradient(135deg, rgba(239, 68, 68, 0.06), rgba(246, 196, 83, 0.04));
  border: 1rpx solid rgba(239, 68, 68, 0.28);
  border-radius: 16rpx;
  padding: 16rpx 20rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.bc-promo-expired {
  background: rgba(255, 255, 255, 0.03);
  border-color: rgba(255, 255, 255, 0.08);
  opacity: 0.6;
}

.bc-promo-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
}

.bc-promo-emoji {
  font-size: 24rpx;
}

.bc-promo-title {
  flex: 1;
  font-size: 24rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  /* 单行省略 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.bc-promo-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
  overflow: hidden;
}

.bc-promo-countdown {
  font-size: 20rpx;
  color: #ef4444;
  font-weight: 700;
}

.bc-promo-countdown-expired {
  color: var(--color-text-muted, #94a3b8);
  font-weight: 500;
}

/* ─── CTA ─── */
.bc-cta-bar {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
}

.bc-cta {
  flex: 1;
  padding: 20rpx 0;
  text-align: center;
  border-radius: 999rpx;
}

.bc-cta-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

.bc-cta-secondary-hover {
  background: rgba(255, 255, 255, 0.16);
}

.bc-cta-primary {
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.18);
}

.bc-cta-primary-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}

.bc-cta-text {
  font-size: 26rpx;
  font-weight: 700;
  color: #ffffff;
}

.bc-cta-text-ghost {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
</style>
