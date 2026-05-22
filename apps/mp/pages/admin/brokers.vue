<script setup lang="ts">
/**
 * Admin 券商管理列表页 (Sprint 11 FE-S11-A01).
 *
 * 路由: ``/pages/admin/brokers``
 *
 * 功能:
 * 1. 顶部 "+ 新建券商" 按钮 → 跳 broker-edit?new=1
 * 2. 顶部切换: 显示软删 / 仅活跃 (默认只看活跃 + 下架, 隐藏软删)
 * 3. 列表 item: logo + name_zh + slug + chip (下架/软删) + display_order
 * 4. 点 item → broker-edit?slug=xxx
 * 5. 不分页 (券商总数 < 30); 下拉刷新走全量拉
 *
 * 鉴权: onLoad 检查 ``authStore.isAdmin``; 非 admin 提示 + 跳回我的页.
 *
 * 与 user 管理页的差异:
 * - 不分页, 不搜索 (券商少, FE 端可直接 filter 数组就够)
 * - 不脱敏 (券商是公开实体)
 */

import { onLoad, onPullDownRefresh } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  listAdminBrokers,
  parseAdminBrokerError,
  type BrokerAdminDetail,
} from '@/api/admin-brokers'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const items = ref<BrokerAdminDetail[]>([])
const phase = ref<'idle' | 'loading' | 'empty' | 'error' | 'ready'>('idle')
const includeDeleted = ref<boolean>(false)

const visibleItems = computed(() => items.value)

async function fetchList() {
  if (!isAdmin.value) return
  phase.value = 'loading'
  try {
    const resp = await listAdminBrokers({
      include_deleted: includeDeleted.value || undefined,
      include_inactive: true,
    })
    items.value = resp.items
    phase.value = resp.items.length === 0 ? 'empty' : 'ready'
  } catch (err) {
    const { code, message } = parseAdminBrokerError(err)
    if (code === 'admin_required') {
      uni.showToast({ title: '权限不足', icon: 'none' })
      setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
      return
    }
    phase.value = 'error'
    uni.showToast({ title: message || '加载失败', icon: 'none' })
  }
}

function toggleIncludeDeleted() {
  includeDeleted.value = !includeDeleted.value
  void fetchList()
}

function gotoNew() {
  uni.navigateTo({ url: '/pages/admin/broker-edit?new=1' })
}

function gotoEdit(slug: string) {
  uni.navigateTo({ url: `/pages/admin/broker-edit?slug=${encodeURIComponent(slug)}` })
}

function statusChip(item: BrokerAdminDetail): { text: string; cls: string } | null {
  if (item.is_deleted) return { text: '软删', cls: 'chip-deleted' }
  if (!item.is_active) return { text: '下架', cls: 'chip-inactive' }
  return null
}

onLoad(async () => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  await fetchList()
})

onPullDownRefresh(async () => {
  await fetchList()
  uni.stopPullDownRefresh()
})
</script>

<template>
  <view class="page">
    <view class="top-actions">
      <view class="filter-chips">
        <view
          class="chip"
          :class="{ 'chip-active': includeDeleted }"
          @tap="toggleIncludeDeleted"
        >
          <text>{{ includeDeleted ? '✓ 含软删' : '含软删' }}</text>
        </view>
      </view>
      <view class="new-btn" @tap="gotoNew">
        <text class="new-btn-text">+ 新建券商</text>
      </view>
    </view>

    <view v-if="phase === 'loading' && items.length === 0" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state">
      <text>暂无券商, 点右上角新建</text>
    </view>

    <view v-else-if="phase === 'error'" class="state">
      <text>加载失败</text>
      <view class="retry-btn" @tap="fetchList">
        <text>重试</text>
      </view>
    </view>

    <view v-else class="list">
      <view
        v-for="item in visibleItems"
        :key="item.broker_id"
        class="item"
        @tap="gotoEdit(item.slug)"
      >
        <view class="item-left">
          <image v-if="item.logo_url" class="logo" :src="item.logo_url" mode="aspectFit" />
          <view v-else class="logo-placeholder">
            <text>{{ item.name_zh.slice(0, 1) }}</text>
          </view>
          <view class="item-text">
            <view class="item-title-row">
              <text class="item-title">{{ item.name_zh }}</text>
              <view v-if="statusChip(item)" :class="['chip-status', statusChip(item)!.cls]">
                <text>{{ statusChip(item)!.text }}</text>
              </view>
            </view>
            <text class="item-slug">{{ item.slug }}</text>
            <text class="item-meta">
              排序 {{ item.display_order }} · {{ item.market_support.join('/') || '未设置市场' }}
            </text>
          </view>
        </view>
        <text class="arrow">›</text>
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
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24rpx;
}

.filter-chips {
  display: flex;
  gap: 16rpx;
}

.chip {
  padding: 12rpx 24rpx;
  border-radius: 32rpx;
  border: 1rpx solid #2a3654;
  background-color: #1a2238;

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

.new-btn {
  padding: 16rpx 32rpx;
  background-color: #3b82f6;
  border-radius: 16rpx;

  .new-btn-text {
    color: #ffffff;
    font-size: 26rpx;
    font-weight: 500;
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
  background-color: #131c30;
  border-radius: 16rpx;
  overflow: hidden;
}

.item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24rpx;
  border-bottom: 1rpx solid #1f2942;

  &:last-child {
    border-bottom: none;
  }
}

.item-left {
  display: flex;
  align-items: center;
  flex: 1;
  min-width: 0;
}

.logo,
.logo-placeholder {
  width: 80rpx;
  height: 80rpx;
  border-radius: 16rpx;
  background-color: #1a2238;
  flex-shrink: 0;
  margin-right: 20rpx;
  display: flex;
  align-items: center;
  justify-content: center;

  text {
    color: #8b9bb8;
    font-size: 32rpx;
    font-weight: 600;
  }
}

.item-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}

.item-title-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
}

.item-title {
  color: #e4e7ee;
  font-size: 30rpx;
  font-weight: 600;
}

.item-slug {
  color: #6b7794;
  font-size: 22rpx;
}

.item-meta {
  color: #8b9bb8;
  font-size: 22rpx;
}

.chip-status {
  padding: 4rpx 12rpx;
  border-radius: 8rpx;

  text {
    font-size: 20rpx;
  }
}

.chip-inactive {
  background-color: rgba(245, 158, 11, 0.18);

  text {
    color: #fbbf24;
  }
}

.chip-deleted {
  background-color: rgba(239, 68, 68, 0.18);

  text {
    color: #fca5a5;
  }
}

.arrow {
  color: #6b7794;
  font-size: 36rpx;
  margin-left: 16rpx;
}
</style>
