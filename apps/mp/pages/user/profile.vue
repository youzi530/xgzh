<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 用户公开资料页 (BUG-S6.8-003 minimal 版).
 *
 * 入口: 社区帖子卡 / 帖子详情 → 点击作者昵称 → ``navigateTo('/pages/user/profile?id=<uuid>')``
 *
 * 字段:
 * - 头像 (空则昵称首字 fallback, 与 community 列表卡同款)
 * - 昵称 (空则 ``"匿名用户"``)
 * - 注册时间 (``"2026-04-15 加入"``, 不暴露具体小时)
 * - 帖子数 (仅 published; 后端已过滤)
 *
 * 错误兜底:
 * - 404 user_not_found → 整页"墓碑"提示 + 返回按钮
 * - 网络错 → 错误 banner + "重试"按钮
 *
 * 不在 minimal 范围:
 * - 该用户的帖子列表 (留 Sprint 6.9 ``with_posts`` 升级)
 * - 关注 / 私信 / 拉黑 (留 Sprint 7+, 需要先建社交关系表)
 */
import { onLoad } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import { fetchUserPublicProfile, type UserPublicProfile } from '@/api/users'
import { APIError } from '@/utils/request'

const profile = ref<UserPublicProfile | null>(null)
const loading = ref(true)
const error = ref<string>('')
const userId = ref<string>('')

const avatarFallback = computed(() => {
  const name = profile.value?.nickname || '匿'
  return name.slice(0, 1)
})

const displayNickname = computed(() => profile.value?.nickname || '匿名用户')

const joinedText = computed(() => {
  const ca = profile.value?.created_at
  if (!ca) return ''
  return `${ca.slice(0, 10)} 加入`
})

async function load() {
  if (!userId.value) {
    error.value = '缺少 user_id 参数'
    loading.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    profile.value = await fetchUserPublicProfile(userId.value)
  } catch (e) {
    if (e instanceof APIError) {
      let code: string | undefined
      if (e.detail && typeof e.detail === 'object' && !Array.isArray(e.detail)) {
        const d = e.detail as { code?: string }
        code = d.code
      }
      if (code === 'user_not_found' || e.statusCode === 404) {
        error.value = '该用户不存在或已注销'
      } else {
        error.value = '加载失败, 请稍后重试'
      }
    } else {
      error.value = '加载失败, 请检查网络'
    }
  } finally {
    loading.value = false
  }
}

function gotoBack() {
  uni.navigateBack({ delta: 1 })
}

onLoad((options) => {
  userId.value = (options?.id as string) || ''
  void load()
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view v-if="loading" class="state">
      <text>加载中…</text>
    </view>

    <view v-else-if="error" class="state">
      <text class="error-text">{{ error }}</text>
      <view class="actions">
        <view class="btn btn-primary" @tap="gotoBack">返回</view>
        <view v-if="userId" class="btn btn-ghost" @tap="load">重试</view>
      </view>
    </view>

    <view v-else-if="profile" class="profile">
      <view class="hero">
        <image
          v-if="profile.avatar_url"
          class="avatar"
          :src="profile.avatar_url"
          mode="aspectFill"
        />
        <view v-else class="avatar avatar-fallback">
          <text class="avatar-text">{{ avatarFallback }}</text>
        </view>
        <text class="nickname">{{ displayNickname }}</text>
        <text v-if="joinedText" class="joined">{{ joinedText }}</text>
      </view>

      <view class="stats">
        <view class="stat">
          <text class="stat-value">{{ profile.posts_count }}</text>
          <text class="stat-label">已发布帖子</text>
        </view>
      </view>

      <view class="hint">
        <text>更多互动功能 (关注 / 帖子列表) 后续上线</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 32rpx 24rpx;
  background: var(--color-bg, #0f172a);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
}
.state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24rpx;
}
.error-text {
  font-size: 28rpx;
  color: var(--color-text-muted, #94a3b8);
}
.actions {
  display: flex;
  gap: 16rpx;
}
.btn {
  padding: 14rpx 36rpx;
  border-radius: 999rpx;
  font-size: 26rpx;
}
.btn-primary {
  background: var(--color-accent, #4f8bff);
  color: #fff;
}
.btn-ghost {
  border: 1rpx solid rgba(148, 163, 184, 0.4);
  color: var(--color-text-muted, #94a3b8);
}

.profile {
  display: flex;
  flex-direction: column;
  gap: 32rpx;
}
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14rpx;
  padding: 48rpx 0 28rpx;
}
.avatar {
  width: 160rpx;
  height: 160rpx;
  border-radius: 50%;
  background: rgba(79, 139, 255, 0.18);
  display: flex;
  align-items: center;
  justify-content: center;
}
.avatar-fallback {
  background: linear-gradient(135deg, #4f8bff, #6f6cff);
}
.avatar-text {
  color: #fff;
  font-size: 60rpx;
  font-weight: 700;
}
.nickname {
  font-size: 40rpx;
  font-weight: 700;
}
.joined {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.stats {
  display: flex;
  gap: 16rpx;
}
.stat {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  padding: 28rpx 16rpx;
  background: rgba(79, 139, 255, 0.08);
  border-radius: 24rpx;
}
.stat-value {
  font-size: 44rpx;
  font-weight: 700;
}
.stat-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.hint {
  margin-top: auto;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  padding: 24rpx 0;
}
</style>
