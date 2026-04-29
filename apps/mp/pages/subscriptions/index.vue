<script setup lang="ts">
/**
 * 中签 tab 主页 (FE-S6-001 占位 → FE-S6-002 / FE-S6-003 接 BE-S6-001/002/003).
 *
 * 路由: ``/pages/subscriptions/index``  (tabBar 第 2 个槽位)
 *
 * 当前阶段 = FE-S6-001 占位:
 * - 仅展示"中签记账即将上线"友好提示 + 引导用户去其它 tab
 * - 后续 FE-S6-002 替换为账户切换器 + 月汇总卡片 + 中签列表
 * - 后续 FE-S6-003 加 ``/pages/subscriptions/edit`` 录入表单页
 *
 * 设计取舍:
 *
 * - **占位页也走暗模式 token**: 用户已经在 me/index.vue 里选好主题,
 *   切到中签 tab 不应该突然变白
 * - **不要进度条 / loading 假象**: 直接说"功能即将上线", 不骗用户
 * - **空状态 illustration 用 emoji**: 上线前 UX 替换为插画
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

function gotoKnowledge() {
  uni.switchTab({ url: '/pages/knowledge/index' })
}
</script>

<template>
  <view class="page">
    <view class="hero">
      <text class="hero-emoji">🎯</text>
      <text class="hero-title">中签收益记账</text>
      <text class="hero-subtitle">单户 · 多户 · 月/年/单股 P&amp;L 汇总</text>
    </view>

    <view class="card">
      <view class="card-head">
        <text class="card-tag">即将上线</text>
        <text class="card-title">本期 Sprint 6 主线 B</text>
      </view>
      <text class="card-desc">
        在自己券商 APP 看到中签后, 录入到这里, 一目了然看本月 / 今年 / 单只新股的真实收益。
        支持多账户(招商 / 华盛 / 富途), 自动算孖展利息 / 手续费 / 浮盈浮亏。
      </text>
      <view class="features">
        <view class="feat-item">
          <text class="feat-emoji">📒</text>
          <text class="feat-text">单户 / 多户切换</text>
        </view>
        <view class="feat-item">
          <text class="feat-emoji">📊</text>
          <text class="feat-text">月 / 年 / 单股汇总</text>
        </view>
        <view class="feat-item">
          <text class="feat-emoji">🧮</text>
          <text class="feat-text">自动算 PnL</text>
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
        <view class="ph-btn" hover-class="ph-btn-hover" :hover-stay-time="80" @tap="gotoKnowledge">
          <text class="ph-btn-emoji">📚</text>
          <text class="ph-btn-text">学知识</text>
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
.features {
  display: flex;
  justify-content: space-between;
  margin-top: 8rpx;
}
.feat-item {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding: 16rpx;
  border-radius: 16rpx;
  background: rgba(79, 139, 255, 0.08);
}
.feat-emoji {
  font-size: 40rpx;
  line-height: 1;
}
.feat-text {
  font-size: 22rpx;
  color: var(--color-text, #e2e8f0);
  text-align: center;
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
