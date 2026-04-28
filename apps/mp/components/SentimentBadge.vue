<script setup lang="ts">
/**
 * 通用情感徽标 (FE-S3-001).
 *
 * 3 色编码 (与 spec/03 §模块二统一色板对齐):
 * - bullish (看多):  绿 #22c55e (积极)
 * - neutral  (中性):  蓝 #4f8bff (中立, 与免费 / 默认色一致)
 * - bearish  (看空):  红 #ef4444 (警示)
 *
 * - sentiment === null 时降级为 "中性" 色, 文案改成 "未打标"; 这是常见态
 *   (打标 worker 还在跑或失败兜底, 列表卡片里大量这种)
 *
 * 三种规格:
 * - size='sm' (默认): 列表卡片用; 字号 20rpx, padding 4×12rpx
 * - size='md': 详情页 hero 用; 字号 24rpx, padding 6×16rpx
 * - size='lg': TL;DR 抽屉 hero 用; 字号 28rpx, padding 8×20rpx
 *
 * 不显得分 ``score``: 用户对 -0.42 / 0.83 这种数字没感觉, 文字标签 + 颜色已经够用.
 */

import { computed } from 'vue'

import type { Sentiment } from '@/api/article'

interface Props {
  sentiment: Sentiment | null
  size?: 'sm' | 'md' | 'lg'
  /** 是否显"未打标" 标签 (列表里 NULL 较多, 显空白比显文字更整洁) */
  showWhenNull?: boolean
}

const props = withDefaults(defineProps<Props>(), {
  size: 'sm',
  showWhenNull: false,
})

const variant = computed<'bullish' | 'neutral' | 'bearish' | 'unlabeled'>(() => {
  if (props.sentiment === 'bullish') return 'bullish'
  if (props.sentiment === 'bearish') return 'bearish'
  if (props.sentiment === 'neutral') return 'neutral'
  return 'unlabeled'
})

const label = computed(() => {
  switch (variant.value) {
    case 'bullish':
      return '看多'
    case 'bearish':
      return '看空'
    case 'neutral':
      return '中性'
    case 'unlabeled':
    default:
      return '未打标'
  }
})

const visible = computed(() => {
  return variant.value !== 'unlabeled' || props.showWhenNull
})
</script>

<template>
  <view
    v-if="visible"
    :class="['sb', `sb-${variant}`, `sb-size-${size}`]"
  >
    <text class="sb-text">{{ label }}</text>
  </view>
</template>

<style lang="scss" scoped>
.sb {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 999rpx;
  border: 1rpx solid;
}

.sb-text {
  font-weight: 700;
  line-height: 1;
}

/* ─── 颜色变体 ─── */
.sb-bullish {
  background: rgba(34, 197, 94, 0.12);
  border-color: rgba(34, 197, 94, 0.4);
}
.sb-bullish .sb-text {
  color: #22c55e;
}

.sb-neutral {
  background: rgba(79, 139, 255, 0.12);
  border-color: rgba(79, 139, 255, 0.4);
}
.sb-neutral .sb-text {
  color: #4f8bff;
}

.sb-bearish {
  background: rgba(239, 68, 68, 0.12);
  border-color: rgba(239, 68, 68, 0.4);
}
.sb-bearish .sb-text {
  color: #ef4444;
}

.sb-unlabeled {
  background: rgba(148, 163, 184, 0.1);
  border-color: rgba(148, 163, 184, 0.28);
}
.sb-unlabeled .sb-text {
  color: #94a3b8;
  font-weight: 500;
}

/* ─── 尺寸变体 ─── */
.sb-size-sm {
  padding: 4rpx 14rpx;
}
.sb-size-sm .sb-text {
  font-size: 20rpx;
}

.sb-size-md {
  padding: 8rpx 20rpx;
}
.sb-size-md .sb-text {
  font-size: 24rpx;
}

.sb-size-lg {
  padding: 12rpx 28rpx;
}
.sb-size-lg .sb-text {
  font-size: 28rpx;
}
</style>
