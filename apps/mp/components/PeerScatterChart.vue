<script setup lang="ts">
/**
 * 行业对比散点图 (FE-S4-002).
 *
 * X 轴: PE; Y 轴: 上市首日涨跌幅 %; dot 着色: self 金色双圈, 同行普通灰.
 * 复用 ``SentimentPieChart`` 同款"纯 SVG, 不引 uCharts"决策 (见 SentimentPieChart §不引 uCharts 的原因)。
 *
 * 数据源: BE-S4-003 ``IPOPeerAggregate.scatter_points`` (≤ 50 dots, 含 self).
 *
 * 兜底:
 * - peer_count < 5 → 散点全空 → 显示"数据不足"提示
 * - dot 缺 pe_ratio → X 落 X-轴 0 (左下角不丢失数据点)
 * - dot 缺 first_day_change_pct → 不画 (空数据无视觉意义)
 *
 * 视觉:
 * - p25 / p75 横虚线 + median 实线 (FE 用户一眼看到自己 vs 中位数 / 四分位)
 * - 0% 横轴零基线醒目 (区分涨 / 跌)
 * - X 轴 PE 范围: 自动 [floor(min/5)*5, ceil(max/5)*5]
 * - Y 轴 涨幅范围: 同上, 但保证 0% 在 viewBox 内
 */

import { computed } from 'vue'

import type { IPOPeerAggregate, IPOPeerScatterPoint } from '@/api/ipo'

const props = defineProps<{
  data: IPOPeerAggregate
  /** SVG 宽度 (rpx); 高度 = 0.75 × width 黄金比 */
  width?: number
}>()

const W = computed(() => props.width ?? 600)
const H = computed(() => Math.round((props.width ?? 600) * 0.75))

// SVG viewBox 内边距 (rpx 单位; padding 给轴 label 用)
const PAD_LEFT = 60
const PAD_RIGHT = 20
const PAD_TOP = 24
const PAD_BOTTOM = 50

// dot 半径 (普通 / self) — viewBox 单位
const DOT_R_NORMAL = 5
const DOT_R_SELF = 10

const insufficient = computed(() => props.data.peer_count < 5)

/** 有效 dot: 至少有 first_day_change_pct (Y 轴) 才能画. PE 缺则 X = 0. */
const validPoints = computed<IPOPeerScatterPoint[]>(() =>
  props.data.scatter_points.filter((p) => p.first_day_change_pct != null),
)

interface AxisRange {
  min: number
  max: number
}

/** X 轴 PE 范围 (向 5 取整) */
const xRange = computed<AxisRange>(() => {
  const xs = validPoints.value
    .map((p) => p.pe_ratio ?? 0)
    .filter((x) => Number.isFinite(x))
  if (xs.length === 0) return { min: 0, max: 50 }
  const min = Math.min(...xs, 0) // PE 一般 ≥ 0, 兜底防异常
  const max = Math.max(...xs, 10)
  return {
    min: Math.floor(min / 5) * 5,
    max: Math.ceil(max / 5) * 5,
  }
})

/** Y 轴 首日涨幅 % 范围 (确保 0% 在内, 向 10 取整) */
const yRange = computed<AxisRange>(() => {
  const ys = validPoints.value
    .map((p) => p.first_day_change_pct as number)
    .filter((y) => Number.isFinite(y))
  if (ys.length === 0) return { min: -20, max: 20 }
  const ymin = Math.min(...ys, 0)
  const ymax = Math.max(...ys, 0)
  return {
    min: Math.floor(ymin / 10) * 10,
    max: Math.ceil(ymax / 10) * 10,
  }
})

/** X 数值 → SVG x 坐标 */
function px(x: number): number {
  const { min, max } = xRange.value
  if (max === min) return PAD_LEFT
  const pct = (x - min) / (max - min)
  return PAD_LEFT + pct * (W.value - PAD_LEFT - PAD_RIGHT)
}

/** Y 数值 → SVG y 坐标 (Y 轴翻转) */
function py(y: number): number {
  const { min, max } = yRange.value
  if (max === min) return H.value - PAD_BOTTOM
  const pct = (y - min) / (max - min)
  return H.value - PAD_BOTTOM - pct * (H.value - PAD_TOP - PAD_BOTTOM)
}

const fdStats = computed(() => props.data.first_day_change_pct)

/** Y 轴 0% 基线 (区分涨/跌) */
const zeroY = computed(() => py(0))

/** X 轴 tick (4 等分) */
const xTicks = computed<{ x: number; label: string }[]>(() => {
  const { min, max } = xRange.value
  const step = (max - min) / 4
  return [0, 1, 2, 3, 4].map((i) => {
    const v = min + step * i
    return { x: px(v), label: v.toFixed(0) }
  })
})

/** Y 轴 tick (5 等分; min / 25 / 50 / 75 / max) */
const yTicks = computed<{ y: number; label: string }[]>(() => {
  const { min, max } = yRange.value
  const step = (max - min) / 4
  return [0, 1, 2, 3, 4].map((i) => {
    const v = min + step * i
    return { y: py(v), label: `${v.toFixed(0)}%` }
  })
})

/** dots — self 排最后画 (z-index 最上, 不被普通 dot 盖住) */
const dotsToRender = computed<IPOPeerScatterPoint[]>(() => {
  const others = validPoints.value.filter((p) => !p.is_self)
  const selves = validPoints.value.filter((p) => p.is_self)
  return [...others, ...selves]
})

/** 数据来源汇总 (peer_count / 行业 / 维度) */
const summary = computed(() => {
  const ind = props.data.industry_l1 ?? '行业'
  return `${ind} · 共 ${props.data.peer_count} 只 listed`
})
</script>

<template>
  <view class="psc">
    <view class="psc-head">
      <text class="psc-title">行业散点图</text>
      <text class="psc-sub">{{ summary }}</text>
    </view>

    <!-- 数据不足兜底 -->
    <view v-if="insufficient" class="psc-empty">
      <text class="psc-empty-emoji">📉</text>
      <text class="psc-empty-text">数据不足 (同行 listed &lt; 5 只)</text>
      <text class="psc-empty-sub">
        当前行业历史样本太少, 无法做有意义的统计对比. 试试看其他热门行业.
      </text>
    </view>

    <!-- 主散点图 -->
    <view v-else class="psc-svg-wrap">
      <svg
        :viewBox="`0 0 ${W} ${H}`"
        :width="`${W}rpx`"
        :height="`${H}rpx`"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- 背景 + 边框 -->
        <rect
          :x="PAD_LEFT"
          :y="PAD_TOP"
          :width="W - PAD_LEFT - PAD_RIGHT"
          :height="H - PAD_TOP - PAD_BOTTOM"
          fill="rgba(255, 255, 255, 0.02)"
          stroke="rgba(255, 255, 255, 0.08)"
          stroke-width="1"
        />

        <!-- Y 轴 0% 零基线 (粗虚线, 区分涨/跌) -->
        <line
          v-if="zeroY > PAD_TOP && zeroY < H - PAD_BOTTOM"
          :x1="PAD_LEFT"
          :x2="W - PAD_RIGHT"
          :y1="zeroY"
          :y2="zeroY"
          stroke="rgba(255, 255, 255, 0.3)"
          stroke-width="1"
          stroke-dasharray="4 4"
        />

        <!-- p25 / p75 横虚线 + median 实线 (first_day_change_pct 维度) -->
        <line
          v-if="fdStats.p25 != null"
          :x1="PAD_LEFT"
          :x2="W - PAD_RIGHT"
          :y1="py(fdStats.p25)"
          :y2="py(fdStats.p25)"
          stroke="rgba(148, 163, 184, 0.5)"
          stroke-width="1"
          stroke-dasharray="3 5"
        />
        <line
          v-if="fdStats.p75 != null"
          :x1="PAD_LEFT"
          :x2="W - PAD_RIGHT"
          :y1="py(fdStats.p75)"
          :y2="py(fdStats.p75)"
          stroke="rgba(148, 163, 184, 0.5)"
          stroke-width="1"
          stroke-dasharray="3 5"
        />
        <line
          v-if="fdStats.median != null"
          :x1="PAD_LEFT"
          :x2="W - PAD_RIGHT"
          :y1="py(fdStats.median)"
          :y2="py(fdStats.median)"
          stroke="#f6c453"
          stroke-width="1.5"
          opacity="0.7"
        />

        <!-- median 标签 -->
        <text
          v-if="fdStats.median != null"
          :x="W - PAD_RIGHT - 4"
          :y="py(fdStats.median) - 4"
          fill="#f6c453"
          font-size="16"
          text-anchor="end"
        >
          中位 {{ fdStats.median.toFixed(1) }}%
        </text>

        <!-- 散点 dots -->
        <circle
          v-for="(d, idx) in dotsToRender"
          :key="`dot-${idx}-${d.code}`"
          :cx="px(d.pe_ratio ?? 0)"
          :cy="py(d.first_day_change_pct as number)"
          :r="d.is_self ? DOT_R_SELF : DOT_R_NORMAL"
          :fill="d.is_self ? '#f6c453' : 'rgba(79, 139, 255, 0.55)'"
          :stroke="d.is_self ? '#fff' : 'transparent'"
          :stroke-width="d.is_self ? 2 : 0"
        />

        <!-- self dot 标签 -->
        <text
          v-for="d in validPoints.filter((p) => p.is_self)"
          :key="`self-label-${d.code}`"
          :x="px(d.pe_ratio ?? 0) + DOT_R_SELF + 4"
          :y="py(d.first_day_change_pct as number) + 4"
          fill="#f6c453"
          font-size="18"
          font-weight="600"
        >
          {{ d.name }}
        </text>

        <!-- X 轴 ticks + label -->
        <g>
          <line
            :x1="PAD_LEFT"
            :y1="H - PAD_BOTTOM"
            :x2="W - PAD_RIGHT"
            :y2="H - PAD_BOTTOM"
            stroke="rgba(255, 255, 255, 0.2)"
            stroke-width="1"
          />
          <g v-for="(t, idx) in xTicks" :key="`xtick-${idx}`">
            <line
              :x1="t.x"
              :y1="H - PAD_BOTTOM"
              :x2="t.x"
              :y2="H - PAD_BOTTOM + 6"
              stroke="rgba(255, 255, 255, 0.2)"
              stroke-width="1"
            />
            <text
              :x="t.x"
              :y="H - PAD_BOTTOM + 22"
              fill="#94a3b8"
              font-size="16"
              text-anchor="middle"
            >
              {{ t.label }}
            </text>
          </g>
          <text
            :x="(PAD_LEFT + (W - PAD_RIGHT)) / 2"
            :y="H - PAD_BOTTOM + 44"
            fill="#94a3b8"
            font-size="18"
            text-anchor="middle"
          >
            发行 PE 倍数
          </text>
        </g>

        <!-- Y 轴 ticks + label -->
        <g>
          <line
            :x1="PAD_LEFT"
            :y1="PAD_TOP"
            :x2="PAD_LEFT"
            :y2="H - PAD_BOTTOM"
            stroke="rgba(255, 255, 255, 0.2)"
            stroke-width="1"
          />
          <g v-for="(t, idx) in yTicks" :key="`ytick-${idx}`">
            <line
              :x1="PAD_LEFT - 6"
              :y1="t.y"
              :x2="PAD_LEFT"
              :y2="t.y"
              stroke="rgba(255, 255, 255, 0.2)"
              stroke-width="1"
            />
            <text
              :x="PAD_LEFT - 8"
              :y="t.y + 4"
              fill="#94a3b8"
              font-size="16"
              text-anchor="end"
            >
              {{ t.label }}
            </text>
          </g>
          <text
            :x="20"
            :y="(PAD_TOP + (H - PAD_BOTTOM)) / 2"
            fill="#94a3b8"
            font-size="18"
            text-anchor="middle"
            :transform="`rotate(-90, 20, ${(PAD_TOP + (H - PAD_BOTTOM)) / 2})`"
          >
            上市首日涨跌 %
          </text>
        </g>
      </svg>

      <!-- 图例 -->
      <view class="psc-legend">
        <view class="psc-legend-item">
          <view class="psc-dot psc-dot-self" />
          <text class="psc-legend-text">本只 IPO</text>
        </view>
        <view class="psc-legend-item">
          <view class="psc-dot psc-dot-normal" />
          <text class="psc-legend-text">同行历史</text>
        </view>
        <view class="psc-legend-item">
          <view class="psc-line psc-line-median" />
          <text class="psc-legend-text">中位数</text>
        </view>
        <view class="psc-legend-item">
          <view class="psc-line psc-line-percentile" />
          <text class="psc-legend-text">25/75 分位</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.psc {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.psc-head {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}
.psc-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.psc-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.psc-empty {
  background: rgba(148, 163, 184, 0.08);
  border: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.2));
  border-radius: 16rpx;
  padding: 48rpx 32rpx;
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.psc-empty-emoji {
  font-size: 48rpx;
}
.psc-empty-text {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
.psc-empty-sub {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
}

.psc-svg-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  background: rgba(255, 255, 255, 0.02);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
  padding: 24rpx 8rpx 16rpx;
}

.psc-legend {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 24rpx;
}
.psc-legend-item {
  display: flex;
  align-items: center;
  gap: 8rpx;
}
.psc-dot {
  width: 16rpx;
  height: 16rpx;
  border-radius: 50%;
}
.psc-dot-self {
  background: #f6c453;
  border: 2rpx solid #fff;
}
.psc-dot-normal {
  background: rgba(79, 139, 255, 0.55);
}
.psc-line {
  width: 32rpx;
  height: 2rpx;
}
.psc-line-median {
  background: #f6c453;
}
.psc-line-percentile {
  background: linear-gradient(
    to right,
    transparent 0%,
    transparent 25%,
    #94a3b8 25%,
    #94a3b8 50%,
    transparent 50%,
    transparent 75%,
    #94a3b8 75%,
    #94a3b8 100%
  );
}
.psc-legend-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
</style>
