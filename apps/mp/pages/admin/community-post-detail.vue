<script setup lang="ts">
/**
 * Admin 社区帖子详情页 (Sprint 11 FE-S11-C06).
 *
 * 路由: ``/pages/admin/community-post-detail?post_id=xxx``
 *
 * 功能:
 * - 看用户原文 content (PIPL: 不能改; 本页只读)
 * - 改 status (chip 点选 5 选 1; 带 "处理原因" 输入)
 * - 改 visibility (软隐藏 public / self_only)
 * - DELETE 强删 (= status=deleted; 提示二次确认)
 */

import { onLoad } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  deleteAdminPost,
  getAdminPostDetail,
  parseAdminCommunityError,
  updateAdminPostStatus,
  updateAdminPostVisibility,
  type AdminPostDetail,
  type AdminPostStatus,
  type AdminPostVisibility,
} from '@/api/admin-community'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const detail = ref<AdminPostDetail | null>(null)
const reasonDraft = ref<string>('')
const phase = ref<'loading' | 'ready' | 'saving' | 'error'>('loading')

const statusOptions: { label: string; value: AdminPostStatus }[] = [
  { label: '已发布', value: 'published' },
  { label: '待审', value: 'pending' },
  { label: '已隐藏', value: 'hidden' },
  { label: '已拒', value: 'rejected' },
]

const visibilityOptions: { label: string; value: AdminPostVisibility }[] = [
  { label: '公开', value: 'public' },
  { label: '仅自己可见', value: 'self_only' },
]

const isDeleted = computed(() => detail.value?.status === 'deleted')

async function loadDetail(postId: string) {
  phase.value = 'loading'
  try {
    detail.value = await getAdminPostDetail(postId)
    reasonDraft.value = detail.value.rejection_reason ?? ''
    phase.value = 'ready'
  } catch (err) {
    const { code, message } = parseAdminCommunityError(err)
    if (code === 'post_not_found') {
      uni.showToast({ title: '帖子不存在', icon: 'none' })
    } else {
      uni.showToast({ title: message, icon: 'none' })
    }
    setTimeout(() => uni.navigateBack(), 800)
  }
}

async function updateStatus(s: AdminPostStatus) {
  if (!detail.value || detail.value.status === s) return
  phase.value = 'saving'
  try {
    detail.value = await updateAdminPostStatus(detail.value.id, {
      status: s,
      reason: reasonDraft.value.trim() || undefined,
    })
    uni.showToast({ title: '已更新', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminCommunityError(err)
    uni.showToast({ title: message || '更新失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

async function updateVisibility(v: AdminPostVisibility) {
  if (!detail.value || detail.value.visibility === v) return
  phase.value = 'saving'
  try {
    detail.value = await updateAdminPostVisibility(detail.value.id, {
      visibility: v,
    })
    uni.showToast({ title: '已更新', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminCommunityError(err)
    uni.showToast({ title: message || '更新失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

async function onDelete() {
  if (!detail.value) return
  const confirm = await new Promise<boolean>((resolve) =>
    uni.showModal({
      title: '确认强删',
      content: '确认软删此帖? 等同于改 status=deleted, 全用户不可见, 可在列表 "已删" 中恢复.',
      confirmText: '确认软删',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    }),
  )
  if (!confirm) return

  phase.value = 'saving'
  try {
    await deleteAdminPost(detail.value.id)
    uni.showToast({ title: '已软删', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 600)
  } catch (err) {
    const { message } = parseAdminCommunityError(err)
    uni.showToast({ title: message || '删除失败', icon: 'none' })
    phase.value = 'ready'
  }
}

async function onRestore() {
  if (!detail.value) return
  // 恢复 = PATCH status=published (默认恢复成 published)
  phase.value = 'saving'
  try {
    detail.value = await updateAdminPostStatus(detail.value.id, {
      status: 'published',
      reason: '恢复',
    })
    uni.showToast({ title: '已恢复', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminCommunityError(err)
    uni.showToast({ title: message || '恢复失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onLoad(async (query: Record<string, string | undefined> | undefined) => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  if (!query?.post_id) {
    uni.showToast({ title: '参数缺失', icon: 'none' })
    setTimeout(() => uni.navigateBack(), 500)
    return
  }
  await loadDetail(query.post_id)
})
</script>

<template>
  <view class="page">
    <view v-if="phase === 'loading'" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="detail" class="content">
      <view v-if="isDeleted" class="banner-deleted">
        <text>此帖已软删. 恢复后才能继续展示.</text>
        <view class="restore-btn" @tap="onRestore">
          <text>恢复</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>用户原文 (只读, PIPL 防篡改)</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">分类</text>
          <text class="meta-val">{{ detail.category }}</text>
        </view>
        <view v-if="detail.related_ipo_code" class="meta-row">
          <text class="meta-key">关联 IPO</text>
          <text class="meta-val">{{ detail.related_ipo_code }}</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">作者</text>
          <text class="meta-val">
            @{{ detail.user_nickname || detail.user_id.slice(0, 8) }}
          </text>
        </view>
        <view class="meta-row">
          <text class="meta-key">发布</text>
          <text class="meta-val">{{ formatTime(detail.created_at) }}</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">点赞 / 评论 / 举报</text>
          <text class="meta-val">
            👍 {{ detail.likes_count }} · 💬 {{ detail.comments_count }} · 🚩
            {{ detail.reports_count }}
          </text>
        </view>
        <view class="user-content">
          <text>{{ detail.content }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>处理状态</text>
        </view>
        <view class="chips">
          <view
            v-for="opt in statusOptions"
            :key="opt.value"
            class="chip"
            :class="{ 'chip-active': detail.status === opt.value }"
            @tap="updateStatus(opt.value)"
          >
            <text>{{ opt.label }}</text>
          </view>
        </view>
        <view v-if="detail.reviewed_at" class="meta-row">
          <text class="meta-key">最后处理</text>
          <text class="meta-val">{{ formatTime(detail.reviewed_at) }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>可见性 (软隐藏不动 status)</text>
        </view>
        <view class="chips">
          <view
            v-for="opt in visibilityOptions"
            :key="opt.value"
            class="chip"
            :class="{ 'chip-active': detail.visibility === opt.value }"
            @tap="updateVisibility(opt.value)"
          >
            <text>{{ opt.label }}</text>
          </view>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>处理原因 (rejection_reason; 留痕)</text>
        </view>
        <textarea
          v-model="reasonDraft"
          class="textarea"
          placeholder="选填; 改 status 时自动带上 (最长 200 字)"
          maxlength="200"
        />
      </view>

      <view v-if="!isDeleted" class="actions">
        <view class="delete-btn" @tap="onDelete">
          <text>软删此帖</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background-color: #0b1220;
  padding: 24rpx 32rpx 200rpx;
}

.state {
  padding: 80rpx 32rpx;
  text-align: center;

  text {
    color: #8b9bb8;
    font-size: 28rpx;
  }
}

.banner-deleted {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 20rpx 24rpx;
  background-color: rgba(239, 68, 68, 0.12);
  border: 1rpx solid #ef4444;
  border-radius: 16rpx;
  margin-bottom: 24rpx;

  text {
    color: #fca5a5;
    font-size: 26rpx;
  }
}

.restore-btn {
  padding: 12rpx 24rpx;
  background-color: #ef4444;
  border-radius: 12rpx;

  text {
    color: #ffffff;
  }
}

.section {
  background-color: #131c30;
  border-radius: 16rpx;
  padding: 24rpx;
  margin-bottom: 24rpx;
}

.section-title {
  margin-bottom: 16rpx;

  text {
    color: #93c5fd;
    font-size: 26rpx;
    font-weight: 600;
  }
}

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8rpx 0;
  border-bottom: 1rpx solid #1f2942;

  &:last-child {
    border-bottom: none;
  }

  .meta-key {
    color: #6b7794;
    font-size: 24rpx;
  }

  .meta-val {
    color: #e4e7ee;
    font-size: 24rpx;
  }
}

.user-content {
  margin-top: 16rpx;
  padding: 20rpx;
  background-color: #0b1220;
  border-radius: 12rpx;

  text {
    color: #e4e7ee;
    font-size: 28rpx;
    line-height: 1.6;
    word-break: break-all;
  }
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
  margin-bottom: 16rpx;
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

.textarea {
  width: 100%;
  min-height: 140rpx;
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;
  color: #e4e7ee;
  font-size: 26rpx;
  box-sizing: border-box;
}

.actions {
  margin-top: 32rpx;
}

.delete-btn {
  padding: 24rpx;
  background-color: rgba(239, 68, 68, 0.18);
  border: 1rpx solid #ef4444;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #fca5a5;
    font-size: 28rpx;
  }
}
</style>
