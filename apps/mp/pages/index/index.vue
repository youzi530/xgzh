<script setup lang="ts">
import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import type { UserPublic } from '@/api/auth'
import { fetchIPOList, type IPOItem, type Market } from '@/api/ipo'
import { useAuthStore } from '@/stores/auth'

const list = ref<IPOItem[]>([])
const loading = ref(false)
const error = ref<string>('')
const market = ref<Market>('HK')

// FE-002: 走 store 响应式订阅, 不再 onShow 手动 refresh; 用户在登录页 setSession
// 后回首页, hero 会自动从"登录/注册"切到头像态
const authStore = useAuthStore()
const { user, loggedIn } = storeToRefs(authStore)

const currentUser = computed<UserPublic | null>(() => user.value)

function nicknameInitial(u: UserPublic): string {
  if (u.nickname && u.nickname.length > 0) return u.nickname.slice(0, 1)
  return u.invite_code.slice(0, 1)
}

function gotoLogin() {
  uni.navigateTo({ url: '/pages/auth/login' })
}

function gotoProfile() {
  uni.navigateTo({ url: '/pages/me/index' })
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const resp = await fetchIPOList(market.value, 20)
    list.value = resp.items
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

function switchMarket(m: Market) {
  if (market.value === m) return
  market.value = m
  list.value = []
  load()
}

function openDetail(item: IPOItem) {
  uni.navigateTo({
    url: `/pages/ipo/detail?code=${encodeURIComponent(item.code)}&name=${encodeURIComponent(item.name)}`,
  })
}

function fmtPrice(item: IPOItem) {
  if (item.issue_price == null) return '--'
  return `${item.issue_currency ?? ''} ${Number(item.issue_price).toFixed(2)}`
}

onShow(() => {
  if (list.value.length === 0) load()
})
onPullDownRefresh(() => load())
</script>

<template>
  <view class="page">
    <view class="hero">
      <view class="hero-left">
        <text class="hero-title">新股智汇</text>
        <text class="hero-subtitle">港 A 股打新 · AI 分析 · 跨境合规</text>
      </view>
      <view v-if="!loggedIn" class="auth-pill" @tap="gotoLogin">
        <text>登录 / 注册</text>
      </view>
      <view v-else class="auth-avatar" @tap="gotoProfile">
        <text class="auth-avatar-text">{{ nicknameInitial(currentUser!) }}</text>
      </view>
    </view>

    <view class="tabs">
      <view :class="['tab', market === 'HK' && 'tab-active']" @tap="switchMarket('HK')">港股</view>
      <view :class="['tab', market === 'A' && 'tab-active']" @tap="switchMarket('A')">A 股</view>
    </view>

    <view v-if="loading && list.length === 0" class="state">
      <text>加载中…</text>
    </view>

    <view v-else-if="error" class="state state-error">
      <text>加载失败：{{ error }}</text>
      <view class="retry" @tap="load">点击重试</view>
    </view>

    <view v-else-if="list.length === 0" class="state">
      <text>暂无数据</text>
    </view>

    <view v-else class="list">
      <view
        v-for="item in list"
        :key="item.code"
        class="card"
        @tap="openDetail(item)"
      >
        <view class="card-row card-row-top">
          <text class="card-name">{{ item.name }}</text>
          <text class="card-code">{{ item.code }}</text>
        </view>
        <view class="card-row">
          <text class="card-meta">{{ item.industry ?? '行业未分类' }}</text>
          <text class="card-price">{{ fmtPrice(item) }}</text>
        </view>
        <view class="card-row">
          <text class="card-meta-sub">
            {{ item.listing_date ? `上市 ${item.listing_date}` : '上市日期待定' }}
          </text>
          <text class="card-meta-sub">
            PE {{ item.pe_ratio ? Number(item.pe_ratio).toFixed(1) : '--' }}
          </text>
        </view>
        <view class="card-cta">
          <text class="cta-text">点击进入 · AI 一键诊断 →</text>
        </view>
      </view>
    </view>

    <view class="footer-disclaimer">
      数据仅供参考，不构成投资建议
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 80rpx;
}
.hero {
  padding: 16rpx 8rpx 24rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
}
.hero-left {
  flex: 1;
  min-width: 0;
}
.hero-title {
  display: block;
  font-size: 48rpx;
  font-weight: 700;
  color: var(--color-text);
}
.hero-subtitle {
  display: block;
  margin-top: 8rpx;
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.auth-pill {
  flex-shrink: 0;
  padding: 12rpx 24rpx;
  border-radius: 999rpx;
  font-size: 24rpx;
  color: var(--color-primary);
  background: rgba(79, 139, 255, 0.1);
  border: 1rpx solid rgba(79, 139, 255, 0.3);
}
.auth-avatar {
  flex-shrink: 0;
  width: 64rpx;
  height: 64rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
  display: flex;
  align-items: center;
  justify-content: center;
}
.auth-avatar-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}
.tabs {
  display: flex;
  gap: 16rpx;
  margin: 16rpx 0 24rpx;
}
.tab {
  padding: 12rpx 32rpx;
  border-radius: 999rpx;
  font-size: 26rpx;
  background: var(--color-surface);
  color: var(--color-text-muted);
  border: 1rpx solid var(--color-border);
}
.tab-active {
  background: var(--color-primary);
  color: #fff;
  border-color: transparent;
}
.state {
  text-align: center;
  padding: 80rpx 0;
  color: var(--color-text-muted);
  font-size: 28rpx;
}
.state-error {
  color: var(--color-danger);
}
.retry {
  margin-top: 16rpx;
  display: inline-block;
  padding: 12rpx 32rpx;
  border-radius: 8rpx;
  background: var(--color-primary);
  color: #fff;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}
.card {
  padding: 24rpx;
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 16rpx;
}
.card-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8rpx;
}
.card-row-top {
  margin-top: 0;
}
.card-name {
  font-size: 30rpx;
  font-weight: 600;
  color: var(--color-text);
}
.card-code {
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.card-meta {
  font-size: 24rpx;
  color: var(--color-text-muted);
}
.card-price {
  font-size: 28rpx;
  color: var(--color-accent);
  font-weight: 600;
}
.card-meta-sub {
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.card-cta {
  margin-top: 16rpx;
  padding-top: 16rpx;
  border-top: 1rpx dashed var(--color-border);
}
.cta-text {
  font-size: 24rpx;
  color: var(--color-primary);
}
.footer-disclaimer {
  margin-top: 32rpx;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted);
}
</style>
