<script setup lang="ts">
/**
 * 多空饼图 (FE-S3-002).
 *
 * 纯 SVG ``<circle>`` + ``stroke-dasharray`` 实现 3 色环形饼图; 不引 uCharts.
 *
 * 不引 uCharts 的原因:
 * 1. 包体积: uCharts ~ 50KB+ gzipped, 仅 TL;DR 抽屉一处用, 性价比低
 * 2. MP-WEIXIN canvas 上小程序基础库 < 2.9 性能差 + 跨端 (H5 / App / MP) 渲染差异大
 * 3. 我们只需 3 色饼图, SVG 6 行写完, 维护成本几乎 0
 *
 * 工作原理:
 *
 *   stroke-dasharray = "<dash-len> <gap>"   // dash-len = 弧长, gap = 周长
 *   stroke-dashoffset 用于"旋转"起点
 *
 *   3 段拼成完整圆周, 每段的 dash-len = ratio × 周长, gap 用周长 - dash-len
 *   起点 dashoffset 累加前面段的 dash-len, 实现"接龙"效果
 *
 * 视觉:
 * - 看多 (绿)  → 看空 (红)  → 中性 (蓝灰), 顺时针, 12 点钟方向起
 * - 中心显总文章数 + "篇"
 * - 三色都为 0 (insufficient_data 兜底) → 显灰圈 + "数据不足" 提示
 */

import { computed } from 'vue'

interface Props {
  bullishRatio: number
  neutralRatio: number
  bearishRatio: number
  /** 文章总数; 中心显示 */
  total: number
  /** SVG 边长 (px); 默认 200 */
  size?: number
}

const props = withDefaults(defineProps<Props>(), {
  size: 200,
})

/** 圆环参数; r 留出 strokeWidth 给边描 */
const RADIUS = 80
const STROKE_WIDTH = 28
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const VIEW_BOX = 200 // SVG viewBox 边长; 与 size 通过 transform 缩放, props.size 仅控外层 px

/**
 * 3 段 dasharray 渲染参数.
 *
 * 每段 strokeDasharray="L C-L"; strokeDashoffset 从 0 开始累加, 每段后偏 L (走到下段).
 * 走"先看多 → 看空 → 中性"顺序与色板配合 (绿 → 红 → 蓝灰).
 */
interface Slice {
  color: string
  /** 弧长占周长的比例 0-1 */
  ratio: number
  /** stroke-dashoffset 起点 (前面段累加) */
  offset: number
}

const slices = computed<Slice[]>(() => {
  const b = Math.max(0, Math.min(1, props.bullishRatio))
  const n = Math.max(0, Math.min(1, props.neutralRatio))
  const r = Math.max(0, Math.min(1, props.bearishRatio))

  let cursor = 0
  const result: Slice[] = []
  if (b > 0) {
    result.push({ color: '#22c55e', ratio: b, offset: cursor })
    cursor += b
  }
  if (r > 0) {
    result.push({ color: '#ef4444', ratio: r, offset: cursor })
    cursor += r
  }
  if (n > 0) {
    result.push({ color: '#94a3b8', ratio: n, offset: cursor })
    cursor += n
  }
  return result
})

/** 是否有有效数据 (所有 ratio 为 0 = insufficient_data, 显灰圈兜底) */
const hasData = computed(() => slices.value.length > 0)

function dashArray(ratio: number): string {
  const arc = ratio * CIRCUMFERENCE
  return `${arc} ${CIRCUMFERENCE - arc}`
}

function dashOffset(offset: number): string {
  // SVG 默认从 3 点钟开始顺时针; 减 0 (从 stroke 起点开始); 但我们 transform rotate(-90)
  // 让起点在 12 点钟, 因此 offset 是"从 12 点开始累加的偏移"
  // dashoffset = -offset × C 让 dasharray 起点向后退到这里
  return String(-offset * CIRCUMFERENCE)
}

/** 百分比格式化为 整数% (45% / 12% 等); 0% 不显 */
function pct(ratio: number): string {
  if (ratio <= 0) return ''
  return `${Math.round(ratio * 100)}%`
}
</script>

<template>
  <view class="pc-wrap" :style="`width: ${size}rpx; height: ${size}rpx;`">
    <view class="pc-svg-wrap">
      <!--
        viewBox 200×200, r=80 → 周长 ~502, strokeWidth 28 → 实际显示半径 ~94
        transform="rotate(-90 100 100)" 让 0 点在 12 点钟方向 (上方)
      -->
      <view class="pc-svg">
        <svg viewBox="0 0 200 200" :width="size" :height="size">
          <!-- 底圈 (insufficient_data 时显示) -->
          <circle
            cx="100"
            cy="100"
            :r="RADIUS"
            fill="none"
            stroke="rgba(148, 163, 184, 0.16)"
            :stroke-width="STROKE_WIDTH"
          />
          <!-- 各段 -->
          <circle
            v-for="(s, i) in slices"
            :key="i"
            cx="100"
            cy="100"
            :r="RADIUS"
            fill="none"
            :stroke="s.color"
            :stroke-width="STROKE_WIDTH"
            :stroke-dasharray="dashArray(s.ratio)"
            :stroke-dashoffset="dashOffset(s.offset)"
            stroke-linecap="butt"
            transform="rotate(-90 100 100)"
          />
        </svg>
      </view>
      <!-- 中心叠加文字 -->
      <view class="pc-center">
        <text v-if="hasData" class="pc-total-num">{{ total }}</text>
        <text v-if="hasData" class="pc-total-label">篇文章</text>
        <text v-else class="pc-total-empty">数据不足</text>
      </view>
    </view>

    <!-- 图例 -->
    <view class="pc-legend">
      <view class="pc-legend-item">
        <view class="pc-dot pc-dot-bull" />
        <text class="pc-legend-label">看多</text>
        <text class="pc-legend-pct">{{ pct(bullishRatio) || '—' }}</text>
      </view>
      <view class="pc-legend-item">
        <view class="pc-dot pc-dot-bear" />
        <text class="pc-legend-label">看空</text>
        <text class="pc-legend-pct">{{ pct(bearishRatio) || '—' }}</text>
      </view>
      <view class="pc-legend-item">
        <view class="pc-dot pc-dot-neut" />
        <text class="pc-legend-label">中性</text>
        <text class="pc-legend-pct">{{ pct(neutralRatio) || '—' }}</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.pc-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16rpx;
}

.pc-svg-wrap {
  position: relative;
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
}

.pc-svg {
  width: 100%;
  height: 100%;
}

.pc-center {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4rpx;
  pointer-events: none;
}

.pc-total-num {
  font-size: 56rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
  line-height: 1;
}

.pc-total-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.pc-total-empty {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}

.pc-legend {
  margin-top: 16rpx;
  display: flex;
  flex-direction: row;
  gap: 24rpx;
}

.pc-legend-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4rpx;
}

.pc-dot {
  width: 16rpx;
  height: 16rpx;
  border-radius: 50%;
}

.pc-dot-bull {
  background: #22c55e;
}
.pc-dot-bear {
  background: #ef4444;
}
.pc-dot-neut {
  background: #94a3b8;
}

.pc-legend-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.pc-legend-pct {
  font-size: 24rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
</style>
