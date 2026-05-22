<script setup lang="ts">
/**
 * Admin 社区帖子管理列表页 (Sprint 11 FE-S11-C06).
 *
 * 路由: ``/pages/admin/community``
 *
 * 功能:
 * 1. 顶部 chip 切换 status filter (全部 / pending / published / rejected / hidden / deleted)
 * 2. 右上角 toggle "只看有举报" (has_reports=true)
 * 3. 列表 item: 内容摘要 + status chip + visibility chip + 计数 + 时间
 * 4. 点 item → 跳详情页 ``/pages/admin/community-post-detail?post_id=xxx``
 * 5. 分页: 下拉刷新 + 触底加载
 */

import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  listAdminPosts,
  parseAdminCommunityError,
  type AdminPostListItem,
  type AdminPostStatus,
} from '@/api/admin-community'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const items = ref<AdminPostListItem[]>([])
const total = ref<number>(0)
const page = ref<number>(1)
const pageSize = 20
const statusFilter = ref<AdminPostStatus | 'all'>('all')
const onlyReports = ref<boolean>(false)
const phase = ref<'idle' | 'loading' | 'loading_more' | 'empty' | 'error' | 'ready'>(
  'idle',
)

const hasMore = computed(() => items.value.length < total.value)

const statusOptions: { label: string; value: AdminPostStatus | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '已发布', value: 'published' },
  { label: '待审', value: 'pending' },
  { label: '已隐藏', value: 'hidden' },
  { label: '已拒', value: 'rejected' },
  { label: '已删', value: 'deleted' },
]

async function fetchPage(isLoadMore = false) {
  if (!isAdmin.value) return
  phase.value = isLoadMore ? 'loading_more' : 'loading'
  try {
    const resp = await listAdminPosts({
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      has_reports: onlyReports.value || undefined,
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
    const { code, message } = parseAdminCommunityError(err)
    if (code === 'admin_required') {
      uni.showToast({ title: '权限不足', icon: 'none' })
      setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
      return
    }
    phase.value = 'error'
    uni.showToast({ title: message || '加载失败', icon: 'none' })
  }
}

function setStatusFilter(s: AdminPostStatus | 'all') {
  if (statusFilter.value === s) return
  statusFilter.value = s
  page.value = 1
  void fetchPage()
}

function toggleOnlyReports() {
  onlyReports.value = !onlyReports.value
  page.value = 1
  void fetchPage()
}

function gotoDetail(postId: string) {
  uni.navigateTo({
    url: `/pages/admin/community-post-detail?post_id=${encodeURIComponent(postId)}`,
  })
}

function statusChip(item: AdminPostListItem): { text: string; cls: string } {
  const s = item.status
  if (s === 'published') return { text: '已发布', cls: 'chip-published' }
  if (s === 'pending') return { text: '待审', cls: 'chip-pending' }
  if (s === 'rejected') return { text: '已拒', cls: 'chip-rejected' }
  if (s === 'hidden') return { text: '已隐藏', cls: 'chip-hidden' }
  return { text: '已删', cls: 'chip-deleted' }
}

function visibilityChip(item: AdminPostListItem): string | null {
  return item.visibility === 'self_only' ? '仅自己可见' : null
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
  return content.length > 60 ? `${content.slice(0, 60)}...` : content
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
        class="chip-reports-toggle"
        :class="{ 'chip-active': onlyReports }"
        @tap="toggleOnlyReports"
      >
        <text>{{ onlyReports ? '✓ 有举报' : '有举报' }}</text>
      </view>
    </view>

    <view v-if="phase === 'loading' && items.length === 0" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state">
      <text>该筛选下暂无帖子</text>
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
        :key="item.id"
        class="item"
        @tap="gotoDetail(item.id)"
      >
        <view class="item-row1">
          <view class="chip-status" :class="statusChip(item).cls">
            <text>{{ statusChip(item).text }}</text>
          </view>
          <view v-if="visibilityChip(item)" class="chip-visibility">
            <text>{{ visibilityChip(item) }}</text>
          </view>
          <view v-if="item.reports_count > 0" class="chip-report-count">
            <text>举报 {{ item.reports_count }}</text>
          </view>
          <text class="time">{{ formatTime(item.created_at) }}</text>
        </view>
        <text class="content">{{ summary(item.content) }}</text>
        <view class="meta-row">
          <text class="meta-user">
            @{{ item.user_nickname || item.user_id.slice(0, 8) }}
          </text>
          <text class="meta-counts">
            👍 {{ item.likes_count }} · 💬 {{ item.comments_count }}
          </text>
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
.chip-reports-toggle {
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
.chip-visibility,
.chip-report-count {
  padding: 4rpx 12rpx;
  border-radius: 8rpx;

  text {
    font-size: 20rpx;
  }
}

.chip-published {
  background-color: rgba(34, 197, 94, 0.18);

  text {
    color: #86efac;
  }
}

.chip-pending {
  background-color: rgba(245, 158, 11, 0.18);

  text {
    color: #fbbf24;
  }
}

.chip-rejected {
  background-color: rgba(239, 68, 68, 0.18);

  text {
    color: #fca5a5;
  }
}

.chip-hidden {
  background-color: rgba(107, 119, 148, 0.18);

  text {
    color: #94a3b8;
  }
}

.chip-deleted {
  background-color: rgba(120, 53, 15, 0.28);

  text {
    color: #fca5a5;
  }
}

.chip-visibility {
  background-color: #1a2238;
  border: 1rpx solid #2a3654;

  text {
    color: #8b9bb8;
  }
}

.chip-report-count {
  background-color: rgba(239, 68, 68, 0.18);
  border: 1rpx solid rgba(239, 68, 68, 0.4);

  text {
    color: #fca5a5;
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

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 22rpx;
}

.meta-user {
  color: #93c5fd;
}

.meta-counts {
  color: #6b7794;
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
