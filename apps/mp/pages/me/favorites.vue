<script setup lang="ts">
/**
 * 我的自选 (FE-006, 依赖 BE-010 / FE-005).
 *
 * 模块:
 * 1. 头部 stats 条: 自选总数 + 当前申购中数量 (引导用户行动)
 * 2. 列表: ``IPOCard`` (default variant) 复用首页样式; 点击跳详情, 长按弹 ActionSheet 二次确认移除
 * 3. 空态: 大文案 + "去发现新股" 按钮 (回首页发现)
 * 4. 错误态: 加载失败时给"点击重试"
 * 5. 下拉刷新: 调 ``favStore.loadOnce(true)`` 强刷
 *
 * 数据流:
 * - 数据全走 ``useFavoritesStore()``: 列表 / 详情页 / 这里共用同一份 items, 不重复拉
 * - 移除走 ``favStore.remove(code)`` (内部乐观更新 + 失败回滚); UI 立即变化无需 await refresh
 * - 登出后 store 自动 reset (auth watch), 但本页 ``onShow`` 还会做一层兜底:
 *   未登录直接 ``uni.reLaunch`` 回首页 (而不是 navigateTo, 防止后退栈错乱)
 *
 * 注意:
 * - ``FavoriteItem`` 字段是 ``user_favorites`` ⨝ ``ipos`` LEFT JOIN 投影, 缺
 *   ``subscribe_start`` / ``subscribe_end`` / ``pe_ratio`` 等; ``IPOCard`` 已对
 *   null 兜底, 但仍需要 adapter 把缺失字段填 null 以满足 ``IPOItem`` 类型
 * - 用户收藏的可能是"HK seed"还没入 ``ipos`` 表的 IPO, ``name`` / ``industry`` 都为
 *   null; adapter 用 ``code`` 兜底, 卡片正常渲染但行业 / 价格灰态
 */

import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  type FavoriteItem,
  parseFavoriteError,
} from '@/api/favorites'
import type { IPOItem } from '@/api/ipo'
import IPOCard from '@/components/IPOCard.vue'
import { useAuthStore } from '@/stores/auth'
import { useFavoritesStore } from '@/stores/favorites'
import { navigateWithParams } from '@/utils/navigate'

const authStore = useAuthStore()
const { loggedIn } = storeToRefs(authStore)

const favStore = useFavoritesStore()
const { items, loaded, loading } = storeToRefs(favStore)

const error = ref<string>('')

const subscribingCount = computed(
  () => items.value.filter((i) => i.status === 'subscribing').length,
)

/**
 * ``FavoriteItem → IPOItem`` 适配, 让自选列表复用 ``IPOCard`` 组件.
 * 缺失的 ``subscribe_start`` / ``subscribe_end`` / ``pe_ratio`` 等填 null,
 * ``IPOCard`` 内部对 null 兜底渲染 ``--`` / "信息待补"。
 */
function toIPOItem(f: FavoriteItem): IPOItem {
  return {
    code: f.code,
    name: f.name ?? f.code,
    market: f.market,
    industry: f.industry,
    issue_price: f.issue_price,
    issue_currency: f.issue_currency,
    listing_date: f.listing_date,
    subscribe_start: null,
    subscribe_end: null,
    pe_ratio: null,
    raised_amount: null,
    one_lot_winning_rate: f.one_lot_winning_rate,
    status: f.status,
    data_source: f.data_source ?? '—',
    updated_at: null,
  }
}

async function load(force = false) {
  error.value = ''
  try {
    await favStore.loadOnce(force)
  } catch (e) {
    error.value = (e as Error).message || '加载失败, 请稍后重试'
  }
}

function refreshAuthGate() {
  if (!loggedIn.value) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  // 第一次进 / 切回前台都 ensure 一下 (loadOnce 内部已对已加载有去重)
  load(false)
}

function openDetail(item: IPOItem) {
  // QA-S5-001 BC-4: 用 navigateWithParams 统一 encode (name 中文跨端关键)
  void navigateWithParams('/pages/ipo/detail', { code: item.code, name: item.name })
}

async function onLongPress(item: FavoriteItem) {
  const action = await new Promise<number | null>((resolve) => {
    uni.showActionSheet({
      itemList: ['取消关注'],
      itemColor: '#ef4444',
      success: (r) => resolve(r.tapIndex),
      fail: () => resolve(null),
    })
  })
  if (action !== 0) return
  // 二次确认 modal: 防止 ActionSheet 误触
  const confirm = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: '确认取消关注',
      content: `不再关注 ${item.name ?? item.code} ?\n之后仍可在详情页重新关注。`,
      cancelText: '取消',
      confirmText: '取消关注',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    })
  })
  if (!confirm) return
  try {
    await favStore.remove(item.code)
    uni.showToast({ title: '已取消关注', icon: 'success' })
  } catch (err) {
    const { code, message } = parseFavoriteError(err)
    if (code === 'favorite_code_invalid') {
      uni.showToast({ title: '股票代码格式不支持', icon: 'none' })
    } else if (code.startsWith('token_')) {
      // 拦截器自动跳登录, 这里不再 toast
    } else {
      uni.showToast({ title: message || '取消失败, 请稍后重试', icon: 'none' })
    }
  }
}

function gotoDiscover() {
  uni.reLaunch({ url: '/pages/index/index' })
}

onShow(() => {
  refreshAuthGate()
})

onPullDownRefresh(async () => {
  await load(true)
  uni.stopPullDownRefresh()
})
</script>

<template>
  <view class="page">
    <view class="legal-banner">
      <text class="legal-banner-text">⚠️ 自选仅记录关注偏好, 不构成投资建议</text>
    </view>

    <view v-if="loaded" class="stats">
      <view class="stat-cell">
        <text class="stat-num">{{ items.length }}</text>
        <text class="stat-label">已关注</text>
      </view>
      <view class="stat-cell stat-cell-hot">
        <text class="stat-num">{{ subscribingCount }}</text>
        <text class="stat-label">正在申购</text>
      </view>
    </view>

    <view v-if="!loaded && loading" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="error" class="state state-error">
      <text>{{ error }}</text>
      <view class="retry" @tap="load(true)">点击重试</view>
    </view>

    <view v-else-if="loaded && items.length === 0" class="empty">
      <text class="empty-icon">★</text>
      <text class="empty-title">还没有自选 IPO</text>
      <text class="empty-desc">在详情页点 ☆ 关注感兴趣的新股, 这里会显示申购窗口和提醒</text>
      <view class="empty-cta" @tap="gotoDiscover">
        <text>去发现新股 →</text>
      </view>
    </view>

    <view v-else class="list">
      <view
        v-for="f in items"
        :key="f.code"
        class="list-row"
        @longpress="onLongPress(f)"
      >
        <IPOCard :item="toIPOItem(f)" @select="openDetail" />
      </view>

      <view class="list-footer">
        <text>长按卡片可取消关注</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.legal-banner {
  background: rgba(246, 196, 83, 0.08);
  border: 1rpx solid rgba(246, 196, 83, 0.25);
  border-radius: 12rpx;
  padding: 16rpx 24rpx;
}
.legal-banner-text {
  font-size: 22rpx;
  color: #f6c453;
}

.stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
}
.stat-cell {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.stat-cell-hot {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.12), rgba(246, 196, 83, 0.04));
  border-color: rgba(246, 196, 83, 0.35);
}
.stat-num {
  font-size: 44rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  font-feature-settings: 'tnum';
}
.stat-cell-hot .stat-num {
  color: #f6c453;
}
.stat-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.state {
  padding: 80rpx 0;
  text-align: center;
  color: var(--color-text-muted, #94a3b8);
  font-size: 26rpx;
}
.state-error {
  color: var(--color-danger, #ef4444);
}
.retry {
  margin-top: 20rpx;
  display: inline-block;
  padding: 12rpx 32rpx;
  background: rgba(79, 139, 255, 0.12);
  border: 1rpx solid rgba(79, 139, 255, 0.35);
  border-radius: 999rpx;
  color: var(--color-primary, #4f8bff);
  font-size: 24rpx;
}

.empty {
  margin-top: 60rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 0 40rpx;
}
.empty-icon {
  font-size: 96rpx;
  color: rgba(246, 196, 83, 0.4);
  line-height: 1;
}
.empty-title {
  margin-top: 24rpx;
  font-size: 32rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.empty-desc {
  margin-top: 12rpx;
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
}
.empty-cta {
  margin-top: 40rpx;
  padding: 20rpx 48rpx;
  background: var(--color-primary, #4f8bff);
  color: #fff;
  border-radius: 999rpx;
  font-size: 28rpx;
  font-weight: 600;
}

.list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.list-row {
  // 长按事件挂在外层, 让 IPOCard 的 @tap 不被吞
}
.list-footer {
  margin-top: 12rpx;
  padding: 16rpx 0 32rpx;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}
</style>
