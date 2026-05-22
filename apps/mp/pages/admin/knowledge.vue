<script setup lang="ts">
/**
 * Admin 知识库管理列表页 (Sprint 11 FE-S11-D06).
 *
 * 路由: ``/pages/admin/knowledge``
 *
 * 功能:
 * 1. 顶部 chip 切换 category filter (全部 / hk / cn / general)
 * 2. 顶部右上角 chip "只看草稿" (is_published=false)
 * 3. 顶部 "新建文章" 按钮
 * 4. 列表 item: title + category chip + 已发布/草稿 chip + view_count + 时间
 * 5. 点 item → 跳详情页 ``/pages/admin/knowledge-edit?article_id=xxx``
 * 6. 分页: 下拉刷新 + 触底加载
 */

import { onLoad, onPullDownRefresh, onReachBottom, onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  listAdminArticles,
  parseAdminKnowledgeError,
  type KnowledgeArticleSummary,
  type KnowledgeCategory,
} from '@/api/admin-knowledge'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const items = ref<KnowledgeArticleSummary[]>([])
const total = ref<number>(0)
const page = ref<number>(1)
const pageSize = 20
const categoryFilter = ref<KnowledgeCategory | 'all'>('all')
const onlyDrafts = ref<boolean>(false)
const phase = ref<'idle' | 'loading' | 'loading_more' | 'empty' | 'error' | 'ready'>(
  'idle',
)

const hasMore = computed(() => items.value.length < total.value)

const categoryOptions: { label: string; value: KnowledgeCategory | 'all' }[] = [
  { label: '全部', value: 'all' },
  { label: '港股打新', value: 'hk' },
  { label: 'A 股', value: 'cn' },
  { label: '通用', value: 'general' },
]

async function fetchPage(isLoadMore = false) {
  if (!isAdmin.value) return
  phase.value = isLoadMore ? 'loading_more' : 'loading'
  try {
    const resp = await listAdminArticles({
      category: categoryFilter.value === 'all' ? undefined : categoryFilter.value,
      is_published: onlyDrafts.value ? false : undefined,
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
    const { code, message } = parseAdminKnowledgeError(err)
    if (code === 'admin_required') {
      uni.showToast({ title: '权限不足', icon: 'none' })
      setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
      return
    }
    phase.value = 'error'
    uni.showToast({ title: message || '加载失败', icon: 'none' })
  }
}

function setCategoryFilter(c: KnowledgeCategory | 'all') {
  if (categoryFilter.value === c) return
  categoryFilter.value = c
  page.value = 1
  void fetchPage()
}

function toggleOnlyDrafts() {
  onlyDrafts.value = !onlyDrafts.value
  page.value = 1
  void fetchPage()
}

function gotoCreate() {
  uni.navigateTo({ url: '/pages/admin/knowledge-edit' })
}

function gotoEdit(articleId: string) {
  uni.navigateTo({
    url: `/pages/admin/knowledge-edit?article_id=${encodeURIComponent(articleId)}`,
  })
}

function categoryLabel(c: KnowledgeCategory): string {
  if (c === 'hk') return '港股'
  if (c === 'cn') return 'A 股'
  return '通用'
}

function levelLabel(level: number): string {
  if (level === 1) return '入门'
  if (level === 2) return '进阶'
  return '实战'
}

function formatTime(iso: string): string {
  const d = new Date(iso)
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${M}-${D} ${h}:${m}`
}

onLoad(async () => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  await fetchPage()
})

// 从 edit 页返回时刷新
onShow(async () => {
  if (isAdmin.value && phase.value !== 'idle') {
    page.value = 1
    await fetchPage()
  }
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
      <scroll-view scroll-x class="category-scroll">
        <view class="category-chips">
          <view
            v-for="opt in categoryOptions"
            :key="opt.value"
            class="chip"
            :class="{ 'chip-active': categoryFilter === opt.value }"
            @tap="setCategoryFilter(opt.value)"
          >
            <text>{{ opt.label }}</text>
          </view>
        </view>
      </scroll-view>
      <view
        class="chip-drafts-toggle"
        :class="{ 'chip-active': onlyDrafts }"
        @tap="toggleOnlyDrafts"
      >
        <text>{{ onlyDrafts ? '✓ 只看草稿' : '只看草稿' }}</text>
      </view>
    </view>

    <view class="create-bar">
      <view class="create-btn" @tap="gotoCreate">
        <text>+ 新建文章</text>
      </view>
    </view>

    <view v-if="phase === 'loading' && items.length === 0" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state">
      <text>该筛选下暂无文章</text>
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
        @tap="gotoEdit(item.id)"
      >
        <view class="item-row1">
          <view class="chip-category">
            <text>{{ categoryLabel(item.category) }}</text>
          </view>
          <view class="chip-level">
            <text>{{ levelLabel(item.level) }}</text>
          </view>
          <text class="time">{{ formatTime(item.updated_at) }}</text>
        </view>
        <text class="title">{{ item.title }}</text>
        <view class="meta-row">
          <text class="slug">{{ item.slug }}</text>
          <text class="counts">👀 {{ item.view_count }}</text>
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
  margin-bottom: 16rpx;
}

.category-scroll {
  flex: 1;
  white-space: nowrap;
}

.category-chips {
  display: inline-flex;
  gap: 12rpx;
}

.chip,
.chip-drafts-toggle {
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

.create-bar {
  margin-bottom: 24rpx;
}

.create-btn {
  padding: 16rpx 32rpx;
  background-color: #3b82f6;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #ffffff;
    font-size: 28rpx;
    font-weight: 600;
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

.chip-category,
.chip-level {
  padding: 4rpx 12rpx;
  border-radius: 8rpx;

  text {
    font-size: 20rpx;
  }
}

.chip-category {
  background-color: rgba(59, 130, 246, 0.18);

  text {
    color: #93c5fd;
  }
}

.chip-level {
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

.title {
  color: #e4e7ee;
  font-size: 30rpx;
  font-weight: 600;
  line-height: 1.4;
}

.meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 22rpx;
}

.slug {
  color: #6b7794;
  font-family: monospace;
}

.counts {
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
