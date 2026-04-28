<script setup lang="ts">
/**
 * 行业分位横条 (FE-S4-002).
 *
 * 5 个维度 (first_day_change_pct / pe_ratio / one_lot_winning_rate /
 * oversubscribe_multiple / raised_amount), 每维显:
 *
 *   维度名      最小────[p25 ███ p50 ███ p75]────最大     mean=X
 *
 * 各维度 stats 全 None (peer_count<5 或 HK 专用字段对 A 股) → 显"数据不足".
 * 与 ``PeerScatterChart`` 同款"纯 SVG, 不引 uCharts"决策.
 */

import { computed } from 'vue'

import type { IPOPeerAggregate, IPOPeerStats } from '@/api/ipo'

interface Dim {
  /** 内部 key (与 IPOPeerAggregate 字段名对齐) */
  key:
    | 'first_day_change_pct'
    | 'pe_ratio'
    | 'one_lot_winning_rate'
    | 'oversubscribe_multiple'
    | 'raised_amount'
  label: string
  /** 数值格式化函数 */
  fmt: (v: number) => string
  /** 大数据时仅 HK 有 (A 股全 None 时 hide 整行) */
  hkOnly?: boolean
}

const DIMS: Dim[] = [
  {
    key: 'first_day_change_pct',
    label: '首日涨跌幅',
    fmt: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`,
  },
  {
    key: 'pe_ratio',
    label: '发行 PE',
    fmt: (v) => v.toFixed(1),
  },
  {
    key: 'oversubscribe_multiple',
    label: '认购倍数',
    fmt: (v) => `${v.toFixed(1)}x`,
    hkOnly: true,
  },
  {
    key: 'one_lot_winning_rate',
    label: '一手中签率',
    fmt: (v) => `${(v * 100).toFixed(1)}%`,
    hkOnly: true,
  },
  {
    key: 'raised_amount',
    label: '募资规模',
    fmt: (v) => {
      if (v >= 1e10) return `${(v / 1e10).toFixed(1)} 百亿`
      if (v >= 1e8) return `${(v / 1e8).toFixed(1)} 亿`
      if (v >= 1e4) return `${(v / 1e4).toFixed(1)} 万`
      return v.toFixed(0)
    },
  },
]

const props = defineProps<{
  data: IPOPeerAggregate
}>()

interface RenderRow {
  dim: Dim
  stats: IPOPeerStats
  /** stats 全 None → 整行降级展示"数据不足" */
  hasData: boolean
  /** p25 在 [min, max] 区间的相对位置 0-1 */
  p25Pct: number
  /** p75 在 [min, max] 区间的相对位置 */
  p75Pct: number
  /** median 位置 */
  medianPct: number
  /** mean 位置 */
  meanPct: number
}

const rows = computed<RenderRow[]>(() => {
  return DIMS.map((dim) => {
    const stats = props.data[dim.key] as IPOPeerStats
    const hasData = stats.mean != null
    let p25Pct = 0
    let p75Pct = 0
    let medianPct = 0
    let meanPct = 0
    if (hasData && stats.min != null && stats.max != null && stats.max > stats.min) {
      const range = stats.max - stats.min
      p25Pct = stats.p25 != null ? (stats.p25 - stats.min) / range : 0
      p75Pct = stats.p75 != null ? (stats.p75 - stats.min) / range : 0
      medianPct = stats.median != null ? (stats.median - stats.min) / range : 0
      meanPct = stats.mean != null ? (stats.mean - stats.min) / range : 0
    }
    return { dim, stats, hasData, p25Pct, p75Pct, medianPct, meanPct }
  })
})

const insufficient = computed(() => props.data.peer_count < 5)
</script>

<template>
  <view class="psb">
    <view class="psb-head">
      <text class="psb-title">5 维分位统计</text>
      <text class="psb-sub">同行 listed {{ data.peer_count }} 只 · 数据范围 [min, max]</text>
    </view>

    <view v-if="insufficient" class="psb-empty">
      <text class="psb-empty-text">同行 listed &lt; 5 只, 分位统计无意义</text>
    </view>

    <view v-else class="psb-rows">
      <view v-for="row in rows" :key="row.dim.key" class="psb-row">
        <view class="psb-row-head">
          <text class="psb-row-label">{{ row.dim.label }}</text>
          <text v-if="row.hasData" class="psb-row-mean">
            均值 {{ row.dim.fmt(row.stats.mean!) }}
          </text>
          <text v-else class="psb-row-na">{{ row.dim.hkOnly ? '港股专用' : '数据不足' }}</text>
        </view>

        <view v-if="row.hasData" class="psb-bar-wrap">
          <!-- 全程灰色基线 [min, max] -->
          <view class="psb-bar-bg" />
          <!-- p25-p75 高亮段 -->
          <view
            class="psb-bar-iqr"
            :style="{
              left: `${row.p25Pct * 100}%`,
              width: `${(row.p75Pct - row.p25Pct) * 100}%`,
            }"
          />
          <!-- median 标记 -->
          <view
            class="psb-bar-median"
            :style="{ left: `${row.medianPct * 100}%` }"
          />
          <!-- mean 标记 (空心圆) -->
          <view
            class="psb-bar-mean"
            :style="{ left: `${row.meanPct * 100}%` }"
          />
        </view>

        <view v-if="row.hasData" class="psb-foot">
          <text class="psb-foot-text">{{ row.dim.fmt(row.stats.min!) }}</text>
          <text class="psb-foot-text psb-foot-mid">
            P25 {{ row.dim.fmt(row.stats.p25!) }} ·
            P75 {{ row.dim.fmt(row.stats.p75!) }}
          </text>
          <text class="psb-foot-text">{{ row.dim.fmt(row.stats.max!) }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.psb {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  background: rgba(255, 255, 255, 0.02);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
  padding: 24rpx;
}
.psb-head {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}
.psb-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.psb-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.psb-empty {
  text-align: center;
  padding: 32rpx 0;
}
.psb-empty-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.psb-rows {
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}
.psb-row {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.psb-row-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.psb-row-label {
  font-size: 26rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.psb-row-mean {
  font-size: 22rpx;
  color: var(--color-accent, #f6c453);
  font-feature-settings: 'tnum';
}
.psb-row-na {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.6;
}

.psb-bar-wrap {
  position: relative;
  height: 24rpx;
  margin: 8rpx 0;
}
.psb-bar-bg {
  position: absolute;
  top: 50%;
  left: 0;
  right: 0;
  height: 4rpx;
  margin-top: -2rpx;
  background: rgba(148, 163, 184, 0.25);
  border-radius: 2rpx;
}
.psb-bar-iqr {
  position: absolute;
  top: 50%;
  height: 12rpx;
  margin-top: -6rpx;
  background: rgba(79, 139, 255, 0.45);
  border-radius: 6rpx;
}
.psb-bar-median {
  position: absolute;
  top: 0;
  width: 4rpx;
  height: 24rpx;
  margin-left: -2rpx;
  background: #f6c453;
  border-radius: 2rpx;
}
.psb-bar-mean {
  position: absolute;
  top: 50%;
  width: 14rpx;
  height: 14rpx;
  margin-top: -7rpx;
  margin-left: -7rpx;
  border-radius: 50%;
  border: 2rpx solid #fff;
  background: transparent;
}

.psb-foot {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 8rpx;
}
.psb-foot-text {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
}
.psb-foot-mid {
  flex: 1;
  text-align: center;
  opacity: 0.85;
}
</style>
