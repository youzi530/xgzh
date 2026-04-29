<script setup lang="ts">
/**
 * 知识 tab 主页 (FE-S6-001 占位 → FE-S6-004 接 BE-S6-004 / OPS-S6-001).
 *
 * 路由: ``/pages/knowledge/index``  (tabBar 第 3 个槽位)
 *
 * 当前阶段 = FE-S6-001 占位:
 * - 仅展示"港 A 股打新知识库即将上线"+ 30 篇 curated 内容预告 + 引导
 * - 后续 FE-S6-004 替换为分类 chip + 卡片列表 + 详情 markdown 渲染
 *
 * 设计取舍同 subscriptions/index.vue 的占位实现.
 */

import { onShow } from '@dcloudio/uni-app'
import { ref } from 'vue'

const visited = ref(false)

onShow(() => {
  visited.value = true
})

function gotoHome() {
  uni.switchTab({ url: '/pages/index/index' })
}

function gotoCommunity() {
  uni.switchTab({ url: '/pages/community/index' })
}
</script>

<template>
  <view class="page">
    <view class="hero">
      <text class="hero-emoji">📚</text>
      <text class="hero-title">港 A 股打新知识库</text>
      <text class="hero-subtitle">入门 · 进阶 · 实战 · 风险</text>
    </view>

    <view class="card">
      <view class="card-head">
        <text class="card-tag">即将上线</text>
        <text class="card-title">本期 Sprint 6 主线 C · 30 篇精选</text>
      </view>
      <text class="card-desc">
        团队自有原创 + 律师审核, 覆盖港股 12 篇 / A 股 12 篇 / 通用 6 篇,
        从 5 个关键日期到孖展认购详解, 一次看懂打新全流程。
      </text>
      <view class="cats">
        <view class="cat-pill cat-hk">
          <text class="cat-text">港股 12 篇</text>
        </view>
        <view class="cat-pill cat-cn">
          <text class="cat-text">A 股 12 篇</text>
        </view>
        <view class="cat-pill cat-general">
          <text class="cat-text">通用 6 篇</text>
        </view>
      </view>
    </view>

    <view class="placeholder">
      <text class="placeholder-text">先逛逛其它入口</text>
      <view class="placeholder-actions">
        <view class="ph-btn" hover-class="ph-btn-hover" :hover-stay-time="80" @tap="gotoHome">
          <text class="ph-btn-emoji">🏠</text>
          <text class="ph-btn-text">看打新</text>
        </view>
        <view class="ph-btn" hover-class="ph-btn-hover" :hover-stay-time="80" @tap="gotoCommunity">
          <text class="ph-btn-emoji">💬</text>
          <text class="ph-btn-text">逛社区</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 48rpx 32rpx 80rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 32rpx;
}
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding: 48rpx 24rpx 24rpx;
}
.hero-emoji {
  font-size: 96rpx;
  line-height: 1;
}
.hero-title {
  font-size: 44rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  margin-top: 16rpx;
}
.hero-subtitle {
  font-size: 26rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}
.card {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 36rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 16rpx;
}
.card-tag {
  font-size: 22rpx;
  padding: 6rpx 16rpx;
  background: rgba(246, 196, 83, 0.15);
  border: 1rpx solid rgba(246, 196, 83, 0.4);
  color: #f6c453;
  border-radius: 999rpx;
}
.card-title {
  font-size: 30rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.card-desc {
  font-size: 26rpx;
  line-height: 1.65;
  color: var(--color-text-muted, #94a3b8);
}
.cats {
  display: flex;
  gap: 16rpx;
  flex-wrap: wrap;
  margin-top: 8rpx;
}
.cat-pill {
  flex: 1;
  text-align: center;
  padding: 16rpx 24rpx;
  border-radius: 16rpx;
  border: 1rpx solid;
}
.cat-hk {
  background: rgba(79, 139, 255, 0.1);
  border-color: rgba(79, 139, 255, 0.4);
}
.cat-cn {
  background: rgba(239, 68, 68, 0.08);
  border-color: rgba(239, 68, 68, 0.35);
}
.cat-general {
  background: rgba(34, 197, 94, 0.08);
  border-color: rgba(34, 197, 94, 0.35);
}
.cat-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  font-weight: 600;
}
.placeholder {
  margin-top: 24rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 24rpx;
}
.placeholder-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.placeholder-actions {
  display: flex;
  gap: 24rpx;
}
.ph-btn {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 20rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.ph-btn-hover {
  background: rgba(255, 255, 255, 0.04);
}
.ph-btn-emoji {
  font-size: 32rpx;
  line-height: 1;
}
.ph-btn-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
</style>
