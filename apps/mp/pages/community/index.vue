<script setup lang="ts">
/**
 * 社区 tab 主页 (FE-S6-001 占位 → FE-S6-005..008 接 BE-S6-005..009).
 *
 * 路由: ``/pages/community/index``  (tabBar 第 4 个槽位)
 *
 * 当前阶段 = FE-S6-001 占位:
 * - 仅展示"社区即将上线"+ UGC 守则预告 + 引导
 * - 后续 FE-S6-005 替换为 feed 卡片流 + 顶部"+ 发帖"按钮
 * - 后续 FE-S6-006/007/008 加发帖 / 详情 / 我的发布等子页
 *
 * 设计取舍: 占位页强调"先审后发"+"友善社区"的合规底色, 让老用户从一开始
 * 就接受社区合规规则, 减少正式上线后被举报投诉的认知差.
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

function gotoSubscriptions() {
  uni.switchTab({ url: '/pages/subscriptions/index' })
}
</script>

<template>
  <view class="page">
    <view class="hero">
      <text class="hero-emoji">💬</text>
      <text class="hero-title">打新社区</text>
      <text class="hero-subtitle">交流 · 经验 · 互助 · 友善</text>
    </view>

    <view class="card">
      <view class="card-head">
        <text class="card-tag">即将上线</text>
        <text class="card-title">本期 Sprint 6 主线 D</text>
      </view>
      <text class="card-desc">
        发帖记录你的打新心得, 评论与他人交流经验, 点赞优秀内容。
        所有内容先审后发, 严禁推荐买卖 / 收益承诺 / 引流私域。
      </text>
      <view class="rules">
        <view class="rule-item">
          <text class="rule-emoji">✅</text>
          <text class="rule-text">分享打新心得 / 中签经验 / 招股书解读</text>
        </view>
        <view class="rule-item">
          <text class="rule-emoji">✅</text>
          <text class="rule-text">理性讨论, 数据为先</text>
        </view>
        <view class="rule-item rule-bad">
          <text class="rule-emoji">❌</text>
          <text class="rule-text">推荐买入 / 收益承诺 / 内幕</text>
        </view>
        <view class="rule-item rule-bad">
          <text class="rule-emoji">❌</text>
          <text class="rule-text">引流私域 / 加群广告</text>
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
        <view class="ph-btn" hover-class="ph-btn-hover" :hover-stay-time="80" @tap="gotoSubscriptions">
          <text class="ph-btn-emoji">🎯</text>
          <text class="ph-btn-text">记中签</text>
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
.rules {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.rule-item {
  display: flex;
  align-items: center;
  gap: 16rpx;
  padding: 16rpx 24rpx;
  background: rgba(34, 197, 94, 0.06);
  border: 1rpx solid rgba(34, 197, 94, 0.25);
  border-radius: 16rpx;
}
.rule-bad {
  background: rgba(239, 68, 68, 0.06);
  border-color: rgba(239, 68, 68, 0.25);
}
.rule-emoji {
  font-size: 30rpx;
  line-height: 1;
}
.rule-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  flex: 1;
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
