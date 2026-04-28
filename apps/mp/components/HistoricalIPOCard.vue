<script setup lang="ts">
/**
 * 历史 IPO 卡片 (FE-S4-001).
 *
 * 与 ``IPOCard`` 区别:
 * - 视觉锚点: ``first_day_change_pct`` 大字 + 红/绿色块 (打新结果一眼可见)
 * - 副信息: 上市日 + industry_l2 + sponsors (前 2)
 * - 不复用 IPOCard 因为视觉权重不同, 历史卡需要"涨跌色块"占据视觉中心
 *
 * 使用:
 *   <HistoricalIPOCard :item="row" @select="handleClick" />
 *
 * BE-S4-003 ``HistoricalIPOItem`` 字段直入.
 */

import { computed } from 'vue'

import type { HistoricalIPOItem } from '@/api/ipo'

const props = defineProps<{
  item: HistoricalIPOItem
}>()

defineEmits<{
  (e: 'select', item: HistoricalIPOItem): void
}>()

/** 首日涨幅文案: 带 + / − 符号 + 1 位小数 (没数据返 '--') */
const fdText = computed<string>(() => {
  const v = props.item.first_day_change_pct
  if (v == null) return '--'
  const num = Number(v)
  const sign = num >= 0 ? '+' : ''
  return `${sign}${num.toFixed(1)}%`
})

/** 首日涨幅色块: ≥ 0 涨绿 / < 0 跌红 / 缺数据灰 */
const fdPalette = computed<{ bg: string; fg: string; border: string }>(() => {
  const v = props.item.first_day_change_pct
  if (v == null) {
    return {
      bg: 'rgba(148, 163, 184, 0.12)',
      fg: '#94a3b8',
      border: 'rgba(148, 163, 184, 0.3)',
    }
  }
  if (Number(v) >= 0) {
    // 港股 A 股配色: 涨用暖橙 (而非红), 与品牌 #f6c453 一致
    return {
      bg: 'rgba(34, 197, 94, 0.15)',
      fg: '#22c55e',
      border: 'rgba(34, 197, 94, 0.4)',
    }
  }
  return {
    bg: 'rgba(239, 68, 68, 0.12)',
    fg: '#ef4444',
    border: 'rgba(239, 68, 68, 0.3)',
  }
})

const listingDateText = computed(() => {
  const ld = props.item.listing_date
  if (!ld) return '上市日待补'
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(ld)
  if (!m) return ld
  return `${m[1]}-${m[2]}-${m[3]}`
})

const oversubscribeText = computed<string | null>(() => {
  const v = props.item.oversubscribe_multiple
  if (v == null) return null
  return `${Number(v).toFixed(1)}x`
})

const winRateText = computed<string | null>(() => {
  const v = props.item.one_lot_winning_rate
  if (v == null) return null
  return `${(Number(v) * 100).toFixed(1)}%`
})

const sponsorsText = computed<string>(() => {
  const s = props.item.sponsors
  if (!s || s.length === 0) return ''
  return s.slice(0, 2).join(' / ')
})
</script>

<template>
  <view
    class="hist-card"
    :style="{
      '--fd-bg': fdPalette.bg,
      '--fd-fg': fdPalette.fg,
      '--fd-border': fdPalette.border,
    }"
    @tap="$emit('select', item)"
  >
    <!-- 头: code + name + 首日涨幅大字 -->
    <view class="hc-head">
      <view class="hc-title">
        <text class="hc-name">{{ item.name }}</text>
        <text class="hc-code">{{ item.code }}</text>
      </view>
      <view class="hc-fd-block">
        <text class="hc-fd-num">{{ fdText }}</text>
        <text class="hc-fd-label">上市首日</text>
      </view>
    </view>

    <!-- 中: 行业 + 上市日 -->
    <view class="hc-mid">
      <view class="hc-tag">
        <text class="hc-tag-text">{{ item.industry || '行业未分类' }}</text>
        <text v-if="item.industry_l2" class="hc-tag-sub">· {{ item.industry_l2 }}</text>
      </view>
      <text class="hc-date">{{ listingDateText }}</text>
    </view>

    <!-- 底: PE / 中签率 / 认购倍数 / sponsors -->
    <view class="hc-foot">
      <view class="hc-meta">
        <text v-if="item.pe_ratio != null" class="hc-meta-item">
          PE {{ Number(item.pe_ratio).toFixed(1) }}
        </text>
        <text v-if="winRateText" class="hc-meta-item">中签 {{ winRateText }}</text>
        <text v-if="oversubscribeText" class="hc-meta-item">认购 {{ oversubscribeText }}</text>
      </view>
      <text v-if="sponsorsText" class="hc-sponsors">{{ sponsorsText }}</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.hist-card {
  position: relative;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.hc-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16rpx;
}
.hc-title {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.hc-name {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.hc-code {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
}

.hc-fd-block {
  flex-shrink: 0;
  padding: 12rpx 20rpx;
  border-radius: 16rpx;
  background: var(--fd-bg);
  border: 1rpx solid var(--fd-border);
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2rpx;
  min-width: 140rpx;
}
.hc-fd-num {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--fd-fg);
  font-feature-settings: 'tnum';
}
.hc-fd-label {
  font-size: 18rpx;
  color: var(--fd-fg);
  opacity: 0.8;
}

.hc-mid {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12rpx;
}
.hc-tag {
  display: flex;
  align-items: baseline;
  gap: 4rpx;
  flex: 1;
  min-width: 0;
  overflow: hidden;
}
.hc-tag-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.hc-tag-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}
.hc-date {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
  flex-shrink: 0;
}

.hc-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12rpx;
}
.hc-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
}
.hc-meta-item {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.hc-sponsors {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  text-align: right;
  max-width: 240rpx;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
</style>
