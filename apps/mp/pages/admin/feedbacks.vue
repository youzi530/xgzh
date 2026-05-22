<script setup lang="ts">
/**
 * Admin 反馈管理列表页 (Sprint 11 FE-S11-B01).
 *
 * 路由: ``/pages/admin/feedbacks``
 *
 * 功能:
 * 1. 顶部 chip 切换 admin_status filter (全部 / pending / reviewed / resolved / closed)
 * 2. 顶部右上角 toggle 含软删
 * 3. 列表 item: 摘要 + category chip + admin_status chip + 时间 + 是否软删
 * 4. 点 item → 跳详情页 ``/pages/admin/feedback-detail?feedback_id=xxx``
 * 5. 分页: 下拉刷新 + 触底加载
 */

import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  listAdminFeedbacks,
  parseAdminFeedbackError,
  type AdminFeedbackListItem,
  type AdminFeedbackStatus,
} from '@/api/admin-feedbacks'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const items = ref<AdminFeedbackListItem[]>([])
const total = ref<number>(0)
const page = ref<number>(1)
const pageSize = 20
const statusFilter = ref<AdminFeedbackStatus | 'all'>('all')
const includeDeleted = ref<boolean>(false)
const phase = ref<'idle' | 'loading' | 'loading_more' | 'empty' | 'error' | 'ready'>(
  'idle',
)

const hasMore = computed(() => items.value.length < total.value)

const statusOptions: { label: string; value: AdminFeedbackStatus | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '待处理', value: 'pending' },
  { label: '已查看', value: 'reviewed' },
  { label: '已解决', value: 'resolved' },
  { label: '已关闭', value: 'closed' },
]

async function fetchPage(isLoadMore = false) {
  if (!isAdmin.value) return
  phase.value = isLoadMore ? 'loading_more' : 'loading'
  try {
    const resp = await listAdminFeedbacks({
      admin_status:
        statusFilter.value === 'all' ? undefined : statusFilter.value,
      include_deleted: includeDeleted.value || undefined,
      page: page.value,
      page_size: pageSize,
    })
    if (isLoadMore) {
      items.value = items.value.concat(resp.items)
    } else {
      items.value = resp.items
    }
    total.value = resp.total
    phase.value = items.value.length === 0 ? 'empty' : 'ready'
  } catch (err) {
    const { code, message } = parseAdminFeedbackError(err)
    if (code === 'admin_required') {
      uni.showToast({ title: '权限不足', icon: 'none' })
      setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
      return
    }
    phase.value = 'error'
    uni.showToast({ title: message || '加载失败', icon: 'none' })
  }
}

function setStatusFilter(s: AdminFeedbackStatus | 'all') {
  if (statusFilter.value === s) return
  statusFilter.value = s
  page.value = 1
  void fetchPage()
}

function toggleIncludeDeleted() {
  includeDeleted.value = !includeDeleted.value
  page.value = 1
  void fetchPage()
}

function gotoDetail(feedbackId: string) {
  uni.navigateTo({
    url: `/pages/admin/feedback-detail?feedback_id=${encodeURIComponent(feedbackId)}`,
  })
}

function statusChip(item: AdminFeedbackListItem): { text: string; cls: string } {
  const s = item.admin_status ?? 'pending'
  if (item.is_deleted) return { text: '软删', cls: 'chip-deleted' }
  if (s === 'pending') return { text: '待处理', cls: 'chip-pending' }
  if (s === 'reviewed') return { text: '已查看', cls: 'chip-reviewed' }
  if (s === 'resolved') return { text: '已解决', cls: 'chip-resolved' }
  return { text: '已关闭', cls: 'chip-closed' }
}

function categoryChip(c: string): string {
  if (c === 'bug') return 'BUG'
  if (c === 'feature') return '建议'
  if (c === 'content') return '内容'
  return '其它'
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${M}-${D} ${h}:${m}`
}

function summary(content: string): string {
  return content.length > 50 ? `${content.slice(0, 50)}...` : content
}

onLoad(async () => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  await fetchPage()
})

onPullDownRefresh(async () => {
  page.value = 1
  await fetchPage()
  uni.stopPullDownRefresh()
})

onReachBottom(async () => {
  if (!hasMore.value || phase.value === 'loading_more') return
  page.value += 1
  await fetchPage(true)
})
</script>

<template>
  <view class="page">
    <view class="top-actions">
      <scroll-view scroll-x class="status-scroll">
        <view class="status-chips">
          <view
            v-for="opt in statusOptions"
            :key="opt.value"
            class="chip"
            :class="{ 'chip-active': statusFilter === opt.value }"
            @tap="setStatusFilter(opt.value)"
          >
            <text>{{ opt.label }}</text>
          </view>
        </view>
      </scroll-view>
      <view
        class="chip-deleted-toggle"
        :class="{ 'chip-active': includeDeleted }"
        @tap="toggleIncludeDeleted"
      >
        <text>{{ includeDeleted ? '✓ 含软删' : '含软删' }}</text>
      </view>
    </view>

    <view v-if="phase === 'loading' && items.length === 0" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state">
      <text>该筛选下暂无反馈</text>
    </view>

    <view v-else-if="phase === 'error'" class="state">
      <text>加载失败</text>
      <view class="retry-btn" @tap="fetchPage(false)">
        <text>重试</text>
      </view>
    </view>

    <view v-else class="list">
      <view
        v-for="item in items"
        :key="item.feedback_id"
        class="item"
        @tap="gotoDetail(item.feedback_id)"
      >
        <view class="item-row1">
          <view class="chip-status" :class="statusChip(item).cls">
            <text>{{ statusChip(item).text }}</text>
          </view>
          <view class="chip-category">
            <text>{{ categoryChip(item.category) }}</text>
          </view>
          <text class="time">{{ formatTime(item.created_at) }}</text>
        </view>
        <text class="content">{{ summary(item.content) }}</text>
        <view v-if="item.admin_note" class="admin-note">
          <text class="note-prefix">💬</text>
          <text class="note-text">{{ summary(item.admin_note) }}</text>
        </view>
      </view>

      <view v-if="phase === 'loading_more'" class="more-state">
        <text>加载中...</text>
      </view>
      <view v-else-if="!hasMore && items.length > 0" class="more-state">
        <text>— 没有更多了 —</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background-color: #0b1220;
  padding: 24rpx 32rpx;
}

.top-actions {
  display: flex;
  align-items: center;
  gap: 16rpx;
  margin-bottom: 24rpx;
}

.status-scroll {
  flex: 1;
  white-space: nowrap;
}

.status-chips {
  display: inline-flex;
  gap: 12rpx;
}

.chip,
.chip-deleted-toggle {
  padding: 10rpx 22rpx;
  border-radius: 32rpx;
  border: 1rpx solid #2a3654;
  background-color: #1a2238;
  white-space: nowrap;

  text {
    font-size: 24rpx;
    color: #8b9bb8;
  }
}

.chip-active {
  border-color: #3b82f6;
  background-color: rgba(59, 130, 246, 0.18);

  text {
    color: #93c5fd;
  }
}

.state {
  padding: 80rpx 32rpx;
  text-align: center;

  text {
    color: #8b9bb8;
    font-size: 28rpx;
  }
}

.retry-btn {
  margin-top: 24rpx;
  display: inline-block;
  padding: 12rpx 32rpx;
  border: 1rpx solid #3b82f6;
  border-radius: 16rpx;

  text {
    color: #93c5fd;
  }
}

.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.item {
  background-color: #131c30;
  border-radius: 16rpx;
  padding: 20rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}

.item-row1 {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.chip-status,
.chip-category {
  padding: 4rpx 12rpx;
  border-radius: 8rpx;

  text {
    font-size: 20rpx;
  }
}

.chip-pending {
  background-color: rgba(245, 158, 11, 0.18);

  text {
    color: #fbbf24;
  }
}

.chip-reviewed {
  background-color: rgba(59, 130, 246, 0.18);

  text {
    color: #93c5fd;
  }
}

.chip-resolved {
  background-color: rgba(34, 197, 94, 0.18);

  text {
    color: #86efac;
  }
}

.chip-closed {
  background-color: rgba(107, 119, 148, 0.18);

  text {
    color: #94a3b8;
  }
}

.chip-deleted {
  background-color: rgba(239, 68, 68, 0.18);

  text {
    color: #fca5a5;
  }
}

.chip-category {
  background-color: #1a2238;
  border: 1rpx solid #2a3654;

  text {
    color: #8b9bb8;
  }
}

.time {
  flex: 1;
  text-align: right;
  color: #6b7794;
  font-size: 22rpx;
}

.content {
  color: #e4e7ee;
  font-size: 28rpx;
  line-height: 1.5;
  word-break: break-all;
}

.admin-note {
  display: flex;
  align-items: flex-start;
  gap: 8rpx;
  padding: 12rpx;
  background-color: rgba(59, 130, 246, 0.08);
  border-left: 4rpx solid #3b82f6;
  border-radius: 8rpx;

  .note-prefix {
    color: #93c5fd;
    font-size: 22rpx;
  }

  .note-text {
    flex: 1;
    color: #93c5fd;
    font-size: 22rpx;
  }
}

.more-state {
  padding: 32rpx;
  text-align: center;

  text {
    color: #6b7794;
    font-size: 22rpx;
  }
}
</style>
