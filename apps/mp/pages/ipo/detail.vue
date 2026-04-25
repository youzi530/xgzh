<script setup lang="ts">
import { onLoad } from '@dcloudio/uni-app'
import { ref } from 'vue'

import { fetchIPODetail, type IPOItem } from '@/api/ipo'

const code = ref('')
const name = ref('')
const item = ref<IPOItem | null>(null)
const loading = ref(false)
const error = ref('')

onLoad((query) => {
  code.value = decodeURIComponent((query?.code as string) ?? '')
  name.value = decodeURIComponent((query?.name as string) ?? '')
  if (code.value) load()
})

async function load() {
  loading.value = true
  error.value = ''
  try {
    item.value = await fetchIPODetail(code.value)
  } catch (e) {
    const msg = (e as Error).message
    if (msg.includes('404')) {
      error.value = '该新股暂未在数据源命中，仍可使用 AI 诊断进行通用分析'
    } else {
      error.value = msg
    }
  } finally {
    loading.value = false
  }
}

function gotoAgent() {
  uni.navigateTo({
    url: `/pages/ipo/agent?code=${encodeURIComponent(code.value)}&name=${encodeURIComponent(name.value || item.value?.name || '')}`,
  })
}
</script>

<template>
  <view class="page">
    <view class="header">
      <text class="title">{{ item?.name || name || code }}</text>
      <text class="code">{{ code }}</text>
    </view>

    <view v-if="loading" class="state">加载中…</view>
    <view v-else-if="error && !item" class="state state-warn">{{ error }}</view>

    <view v-if="item" class="info-grid">
      <view class="info-cell">
        <text class="info-label">市场</text>
        <text class="info-value">{{ item.market }}</text>
      </view>
      <view class="info-cell">
        <text class="info-label">行业</text>
        <text class="info-value">{{ item.industry || '--' }}</text>
      </view>
      <view class="info-cell">
        <text class="info-label">发行价</text>
        <text class="info-value">
          {{ item.issue_price != null ? `${item.issue_currency ?? ''} ${Number(item.issue_price).toFixed(2)}` : '--' }}
        </text>
      </view>
      <view class="info-cell">
        <text class="info-label">PE</text>
        <text class="info-value">{{ item.pe_ratio != null ? Number(item.pe_ratio).toFixed(2) : '--' }}</text>
      </view>
      <view class="info-cell">
        <text class="info-label">上市日期</text>
        <text class="info-value">{{ item.listing_date || '--' }}</text>
      </view>
      <view class="info-cell">
        <text class="info-label">中签率</text>
        <text class="info-value">
          {{ item.one_lot_winning_rate != null ? `${(Number(item.one_lot_winning_rate) * 100).toFixed(2)}%` : '--' }}
        </text>
      </view>
    </view>

    <view class="cta-block" @tap="gotoAgent">
      <view class="cta-title">⚡ AI 一键诊断</view>
      <view class="cta-desc">基于 DeepSeek-V3，输出基本面、风险、多空观点</view>
      <view class="cta-arrow">→</view>
    </view>

    <view class="disclaimer">
      数据来源：AKShare（聚合公开市场数据），最终以官方招股书 / 公告为准。
      <br />
      本内容仅供参考，不构成投资建议。
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
}
.header {
  margin-bottom: 24rpx;
}
.title {
  display: block;
  font-size: 40rpx;
  font-weight: 700;
  color: var(--color-text);
}
.code {
  display: block;
  margin-top: 4rpx;
  color: var(--color-text-muted);
  font-size: 24rpx;
}
.state {
  padding: 60rpx 0;
  text-align: center;
  color: var(--color-text-muted);
  font-size: 26rpx;
}
.state-warn {
  color: var(--color-accent);
}
.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
  margin-bottom: 32rpx;
}
.info-cell {
  padding: 20rpx;
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 12rpx;
}
.info-label {
  display: block;
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.info-value {
  display: block;
  margin-top: 8rpx;
  font-size: 30rpx;
  color: var(--color-text);
  font-weight: 600;
}
.cta-block {
  position: relative;
  padding: 32rpx;
  border-radius: 16rpx;
  background: linear-gradient(135deg, #4f8bff, #7c3aed);
  color: #fff;
}
.cta-title {
  font-size: 34rpx;
  font-weight: 700;
}
.cta-desc {
  margin-top: 8rpx;
  font-size: 24rpx;
  opacity: 0.9;
}
.cta-arrow {
  position: absolute;
  top: 50%;
  right: 32rpx;
  transform: translateY(-50%);
  font-size: 40rpx;
}
.disclaimer {
  margin-top: 32rpx;
  font-size: 22rpx;
  color: var(--color-text-muted);
  line-height: 1.6;
}
</style>
