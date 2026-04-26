<script setup lang="ts">
/**
 * 打新日历 (FE-004 视图组件).
 *
 * 把 IPO 列表按"关键日期"分组成时间轴 (横向) + 该日期下的卡片纵向堆叠.
 * 设计取舍:
 * - 关键日期取 ``subscribe_start`` (申购开始) 优先, 没有就 ``listing_date`` (上市日)
 *   兜底; 都没有则归到"日期待定"组.
 * - 港 A 跨市场字段一致, 同一组件可双市场用.
 * - 仅展示后端返回的 IPO; 不去拉额外接口, 避免拉一份"日历专用数据" (后端没有).
 *   可视范围限于 list 已加载的部分; 用户翻到下一页才能看到下一段日期 (与瀑布流
 *   保持同一数据源, 不复杂化分页逻辑)。
 *
 * 视觉:
 * - 顶部横滚日期 chip (含 IPO 数量徽标), 点击 chip 滚动到对应日期组
 * - 主区按日期分段, 每段头是日期 + 数量, 下面纵向堆叠 IPOCard
 */

import { computed, ref } from 'vue'

import type { IPOItem } from '@/api/ipo'
import IPOCard from './IPOCard.vue'

const props = defineProps<{
  items: IPOItem[]
}>()

defineEmits<{
  (e: 'tap', item: IPOItem): void
}>()

interface DateGroup {
  /** "2026-04-30" 或 "tbd" (待定) */
  key: string
  label: string
  items: IPOItem[]
}

const groups = computed<DateGroup[]>(() => {
  const map = new Map<string, IPOItem[]>()
  for (const item of props.items) {
    const day = pickDay(item)
    const arr = map.get(day) ?? []
    arr.push(item)
    map.set(day, arr)
  }
  // 升序排列; "tbd" 永远沉底
  const keys = Array.from(map.keys()).sort((a, b) => {
    if (a === 'tbd') return 1
    if (b === 'tbd') return -1
    return a < b ? -1 : a > b ? 1 : 0
  })
  return keys.map((k) => ({
    key: k,
    label: k === 'tbd' ? '日期待定' : fmtCalendarLabel(k),
    items: map.get(k)!,
  }))
})

function pickDay(item: IPOItem): string {
  // 优先级: 申购开始 > 上市日; 后端给的可能是 "2026-04-30T09:30+08:00",
  // 截到日即可
  const raw = item.subscribe_start ?? item.listing_date
  if (!raw) return 'tbd'
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(raw)
  return m ? `${m[1]}-${m[2]}-${m[3]}` : 'tbd'
}

function fmtCalendarLabel(iso: string): string {
  // 输入 "2026-04-30" → 输出 "04/30 周X" 给日历轴用
  const m = /(\d{4})-(\d{2})-(\d{2})/.exec(iso)
  if (!m) return iso
  const dt = new Date(`${m[1]}-${m[2]}-${m[3]}T00:00:00`)
  const week = ['日', '一', '二', '三', '四', '五', '六'][dt.getDay()]
  return `${m[2]}/${m[3]} 周${week}`
}

const focusKey = ref<string | null>(null)

function focus(key: string) {
  focusKey.value = key
}
</script>

<template>
  <view class="cal-root">
    <view v-if="groups.length === 0" class="cal-empty">
      <text>暂无日历数据</text>
    </view>

    <template v-else>
      <scroll-view scroll-x class="cal-axis" :show-scrollbar="false">
        <view
          v-for="g in groups"
          :key="`chip-${g.key}`"
          :class="['cal-chip', focusKey === g.key && 'cal-chip-active']"
          @tap="focus(g.key)"
        >
          <text class="cal-chip-label">{{ g.label }}</text>
          <text class="cal-chip-badge">{{ g.items.length }}</text>
        </view>
      </scroll-view>

      <view class="cal-body">
        <view
          v-for="g in groups"
          :id="`grp-${g.key}`"
          :key="`grp-${g.key}`"
          :class="['cal-group', focusKey === g.key && 'cal-group-focus']"
        >
          <view class="cal-group-head">
            <text class="cal-group-label">{{ g.label }}</text>
            <text class="cal-group-count">共 {{ g.items.length }} 只</text>
          </view>
          <view class="cal-group-list">
            <IPOCard
              v-for="item in g.items"
              :key="item.code"
              :item="item"
              @tap="(i) => $emit('tap', i)"
            />
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.cal-root {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.cal-empty {
  text-align: center;
  padding: 80rpx 0;
  color: var(--color-text-muted, #94a3b8);
  font-size: 28rpx;
}

.cal-axis {
  white-space: nowrap;
  padding: 4rpx 0;
}
.cal-chip {
  display: inline-flex;
  align-items: center;
  gap: 12rpx;
  padding: 12rpx 24rpx;
  margin-right: 12rpx;
  border-radius: 999rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
}
.cal-chip-active {
  background: var(--color-primary, #4f8bff);
  border-color: transparent;

  .cal-chip-label,
  .cal-chip-badge {
    color: #fff;
  }
}
.cal-chip-label {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}
.cal-chip-badge {
  font-size: 22rpx;
  padding: 2rpx 10rpx;
  border-radius: 999rpx;
  background: rgba(246, 196, 83, 0.2);
  color: #f6c453;
  font-feature-settings: 'tnum';
}

.cal-body {
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}
.cal-group {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.cal-group-head {
  display: flex;
  align-items: baseline;
  gap: 16rpx;
}
.cal-group-label {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.cal-group-count {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.cal-group-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.cal-group-focus {
  // 弱化的"被点中"反馈; 不挪滚动条因为 uniapp scroll-view 不支持 anchor
  .cal-group-label {
    color: var(--color-primary, #4f8bff);
  }
}
</style>
