<script setup lang="ts">
/**
 * 关注 / 收藏按钮 (FE-005, 复用到 FE-006 自选列表的"长按移除").
 *
 * 数据流:
 * - state 来自 ``useFavoritesStore().isFavored(code)``, 响应式
 * - 点击 → 已登录: 调 ``add`` / ``remove`` (store 内部乐观更新 + 错误回滚)
 *         → 未登录: 弹 modal "登录后才能收藏", 引导跳登录
 * - 操作中 ``loading`` 防止双击; 错误码 ``favorite_code_invalid`` 用 toast 提示
 *
 * 视觉:
 * - 未关注: 描边款 (空心 ☆)
 * - 已关注: 实心款 (★ + 文字"已关注")
 * - 操作中: 半透明 + "..." 占位文字
 */

import { computed, ref } from 'vue'
import { storeToRefs } from 'pinia'

import { parseFavoriteError } from '@/api/favorites'
import { useAuthStore } from '@/stores/auth'
import { useFavoritesStore } from '@/stores/favorites'

const props = defineProps<{
  code: string
  /** 视觉密度: ``default`` 详情页右上, ``compact`` 列表卡片角标 */
  size?: 'default' | 'compact'
}>()

defineEmits<{
  (e: 'changed', favored: boolean): void
}>()

const favStore = useFavoritesStore()
const authStore = useAuthStore()
const { loggedIn } = storeToRefs(authStore)

const loading = ref(false)
const favored = computed(() => favStore.isFavored(props.code))

async function gotoLogin() {
  const r = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: '登录后才能收藏',
      content: '收藏是登录后专属功能, 是否前往登录?',
      cancelText: '稍后',
      confirmText: '去登录',
      success: (res) => resolve(!!res.confirm),
      fail: () => resolve(false),
    })
  })
  if (r) uni.navigateTo({ url: '/pages/auth/login' })
}

async function toggle() {
  if (loading.value) return
  if (!loggedIn.value) {
    await gotoLogin()
    return
  }
  loading.value = true
  const wasFavored = favored.value
  try {
    if (wasFavored) {
      await favStore.remove(props.code)
      uni.showToast({ title: '已取消关注', icon: 'none' })
    } else {
      await favStore.add(props.code)
      uni.showToast({ title: '已加入自选', icon: 'success' })
    }
    // store 内部已乐观更新 + API 同步; 这里只通知父级
    // (favored.value 已是新值, 因为 isFavored 是 computed)
  } catch (e) {
    const { code, message } = parseFavoriteError(e)
    if (code === 'favorite_code_invalid') {
      uni.showToast({ title: '股票代码格式不支持', icon: 'none' })
    } else if (code.startsWith('token_')) {
      // 拦截器会自动跳登录, 这里不再 toast
    } else {
      uni.showToast({ title: message || '操作失败, 请稍后重试', icon: 'none' })
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <view
    :class="[
      'fav-btn',
      `fav-btn-${size ?? 'default'}`,
      favored && 'fav-btn-on',
      loading && 'fav-btn-loading',
    ]"
    @tap="toggle"
  >
    <text class="fav-icon">{{ favored ? '★' : '☆' }}</text>
    <text v-if="size !== 'compact'" class="fav-label">
      {{ loading ? '...' : favored ? '已关注' : '关注' }}
    </text>
  </view>
</template>

<style lang="scss" scoped>
.fav-btn {
  display: inline-flex;
  align-items: center;
  gap: 8rpx;
  padding: 10rpx 24rpx;
  border-radius: 999rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.1));
  background: var(--color-surface, #131a2c);
  color: var(--color-text-muted, #94a3b8);
  font-size: 26rpx;
  transition: all 0.15s ease;
}
.fav-btn-on {
  border-color: rgba(246, 196, 83, 0.5);
  background: rgba(246, 196, 83, 0.1);
  color: #f6c453;
}
.fav-btn-loading {
  opacity: 0.6;
}
.fav-icon {
  font-size: 28rpx;
  line-height: 1;
}
.fav-label {
  font-size: 24rpx;
  font-weight: 500;
}

.fav-btn-compact {
  padding: 6rpx 12rpx;
  font-size: 24rpx;
  .fav-icon {
    font-size: 26rpx;
  }
}
</style>
