<script setup lang="ts">
/**
 * IPO 卡片 (FE-004 复用组件).
 *
 * 同时被首页瀑布流 / 今日打新置顶卡 / 自选列表 (FE-006) 复用; 视觉上提供两种密度:
 * - default: 列表用, 紧凑两行
 * - hero: 首页"今日打新"置顶用, 三行 + 强调申购窗口倒计时
 *
 * 不在这里做的:
 * - 关注按钮 (那是 FE-005 详情页的; 列表卡片不放收藏点击, 防误触)
 * - AI 诊断按钮 (走详情页 CTA)
 */

import { computed } from 'vue'

import {
  type IPOItem,
  type IPOStatus,
  statusLabel,
  statusPalette,
} from '@/api/ipo'

const props = withDefaults(
  defineProps<{
    item: IPOItem
    /** 视觉密度; ``hero`` 用于今日打新置顶卡, ``default`` 列表用 */
    variant?: 'default' | 'hero'
  }>(),
  {
    variant: 'default',
  },
)

// 不能用 'tap' 作为 emit 名: 小程序里 @tap 是 view 元素原生事件关键字, 外层
// `<IPOCard @tap="cb">` 会被 mp-weixin 编译器解析成监听根 view 的原生 tap 事件
// (回调收到 TouchEvent 而不是 emit 的 item) → 用 'select' 避开冲突。
defineEmits<{
  (e: 'select', item: IPOItem): void
}>()

const palette = computed(() => statusPalette(props.item.status))
const label = computed(() => statusLabel(props.item.status))

/**
 * 卡片主行右侧展示的"状态副标题": 决定显示申购窗口 / 上市日期 / 默认占位.
 *  - subscribing → "申购截止 MM-DD"  (优先级最高, 引导用户行动)
 *  - upcoming    → "上市 MM-DD" 或 "申购 MM-DD - MM-DD"
 *  - listed      → "上市 MM-DD"
 *  - withdrawn   → "已撤回"
 *  - unknown     → "信息待补"
 */
const subStatus = computed<string>(() => {
  const i = props.item
  if (i.status === 'subscribing' && i.subscribe_end) {
    return `申购截止 ${fmtMD(i.subscribe_end)}`
  }
  if (i.status === 'upcoming') {
    if (i.subscribe_start && i.subscribe_end) {
      return `申购 ${fmtMD(i.subscribe_start)} - ${fmtMD(i.subscribe_end)}`
    }
    if (i.listing_date) return `上市 ${fmtMD(i.listing_date)}`
    return '日期待定'
  }
  if (i.status === 'listed' && i.listing_date) {
    return `上市 ${fmtMD(i.listing_date)}`
  }
  if (i.status === 'withdrawn') return '已撤回'
  return '信息待补'
})

const issuePriceText = computed(() => {
  const i = props.item
  if (i.issue_price == null) return '--'
  return `${i.issue_currency ?? ''} ${Number(i.issue_price).toFixed(2)}`.trim()
})

const peText = computed(() => {
  const v = props.item.pe_ratio
  if (v == null) return '--'
  return Number(v).toFixed(1)
})

const winRateText = computed(() => {
  const v = props.item.one_lot_winning_rate
  if (v == null) return null
  return `${(Number(v) * 100).toFixed(1)}%`
})

function fmtMD(s: string): string {
  // s 可以是 "2026-04-30" / "2026-04-30T09:30:00+08:00" / "2026-04-30 09:30:00";
  // 截到日就够 (列表用, 不需要时分秒)
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(s)
  if (!m) return s
  return `${m[2]}-${m[3]}`
}
</script>

<template>
  <view
    :class="['ipo-card', `ipo-card-${variant}`]"
    :style="{
      '--status-bg': palette.bg,
      '--status-fg': palette.fg,
      '--status-border': palette.border,
    }"
    @tap="$emit('select', item)"
  >
    <view class="ipo-status">
      <text class="ipo-status-text">{{ label }}</text>
    </view>

    <view class="ipo-head">
      <text class="ipo-name">{{ item.name }}</text>
      <text class="ipo-code">{{ item.code }}</text>
    </view>

    <view class="ipo-mid">
      <text class="ipo-industry">{{ item.industry || '行业未分类' }}</text>
      <text class="ipo-price">{{ issuePriceText }}</text>
    </view>

    <view class="ipo-foot">
      <text class="ipo-substatus">{{ subStatus }}</text>
      <view class="ipo-meta">
        <text class="ipo-meta-item">PE {{ peText }}</text>
        <text v-if="winRateText" class="ipo-meta-item">中签 {{ winRateText }}</text>
      </view>
    </view>

    <view v-if="variant === 'hero'" class="ipo-cta">
      <text class="ipo-cta-text">点击查看详情 · AI 一键诊断 →</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.ipo-card {
  position: relative;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.ipo-status {
  position: absolute;
  top: 20rpx;
  right: 20rpx;
  padding: 4rpx 16rpx;
  border-radius: 999rpx;
  background: var(--status-bg);
  border: 1rpx solid var(--status-border);
}
.ipo-status-text {
  font-size: 20rpx;
  color: var(--status-fg);
  font-weight: 600;
  letter-spacing: 1rpx;
}

.ipo-head {
  display: flex;
  align-items: baseline;
  gap: 12rpx;
  padding-right: 120rpx;
}
.ipo-name {
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  flex: 1;
  // 单行省略
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
}
.ipo-code {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-feature-settings: 'tnum';
}

.ipo-mid {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ipo-industry {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.ipo-price {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-accent, #f6c453);
  font-feature-settings: 'tnum';
}

.ipo-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ipo-substatus {
  font-size: 22rpx;
  color: var(--status-fg);
  font-weight: 500;
}
.ipo-meta {
  display: flex;
  gap: 16rpx;
}
.ipo-meta-item {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.ipo-cta {
  margin-top: 8rpx;
  padding-top: 12rpx;
  border-top: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.08));
}
.ipo-cta-text {
  font-size: 24rpx;
  color: var(--color-primary, #4f8bff);
}

// ─── hero variant: 今日打新置顶 ───
.ipo-card-hero {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.08), rgba(79, 139, 255, 0.06));
  border: 1rpx solid rgba(246, 196, 83, 0.3);
  padding: 32rpx;
  gap: 16rpx;
}
.ipo-card-hero .ipo-name {
  font-size: 36rpx;
}
.ipo-card-hero .ipo-price {
  font-size: 32rpx;
}
</style>
