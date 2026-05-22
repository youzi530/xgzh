<script setup lang="ts">
/**
 * Admin 反馈详情页 (Sprint 11 FE-S11-B01).
 *
 * 路由: ``/pages/admin/feedback-detail?feedback_id=xxx``
 *
 * 功能:
 * - 看用户原文 content (PIPL: admin 不能改用户原文; 本页只读)
 * - 改 admin_status (chip 点选)
 * - 改 admin_note (textarea + 保存)
 * - 软删 / 恢复
 */

import { onLoad } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { ref } from 'vue'

import {
  deleteAdminFeedback,
  getAdminFeedbackDetail,
  parseAdminFeedbackError,
  restoreAdminFeedback,
  updateAdminFeedback,
  type AdminFeedbackDetail,
  type AdminFeedbackStatus,
} from '@/api/admin-feedbacks'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const detail = ref<AdminFeedbackDetail | null>(null)
const noteDraft = ref<string>('')
const phase = ref<'loading' | 'ready' | 'saving' | 'error'>('loading')

const statusOptions: { label: string; value: AdminFeedbackStatus }[] = [
  { label: '待处理', value: 'pending' },
  { label: '已查看', value: 'reviewed' },
  { label: '已解决', value: 'resolved' },
  { label: '已关闭', value: 'closed' },
]

async function loadDetail(feedbackId: string) {
  phase.value = 'loading'
  try {
    detail.value = await getAdminFeedbackDetail(feedbackId)
    noteDraft.value = detail.value.admin_note ?? ''
    phase.value = 'ready'
  } catch (err) {
    const { code, message } = parseAdminFeedbackError(err)
    if (code === 'feedback_not_found') {
      uni.showToast({ title: '反馈不存在', icon: 'none' })
    } else {
      uni.showToast({ title: message, icon: 'none' })
    }
    setTimeout(() => uni.navigateBack(), 800)
  }
}

async function updateStatus(s: AdminFeedbackStatus) {
  if (!detail.value || detail.value.admin_status === s) return
  phase.value = 'saving'
  try {
    detail.value = await updateAdminFeedback(detail.value.feedback_id, {
      admin_status: s,
    })
    uni.showToast({ title: '已更新', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminFeedbackError(err)
    uni.showToast({ title: message || '更新失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

async function saveNote() {
  if (!detail.value) return
  if ((detail.value.admin_note ?? '') === noteDraft.value) return
  phase.value = 'saving'
  try {
    detail.value = await updateAdminFeedback(detail.value.feedback_id, {
      admin_note: noteDraft.value,
    })
    uni.showToast({ title: '备注已保存', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminFeedbackError(err)
    uni.showToast({ title: message || '保存失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

async function onDelete() {
  if (!detail.value) return
  const confirm = await new Promise<boolean>((resolve) =>
    uni.showModal({
      title: '确认软删',
      content: '确认软删此反馈? 列表不再展示, 30 天后硬删.',
      confirmText: '确认软删',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    }),
  )
  if (!confirm) return

  phase.value = 'saving'
  try {
    await deleteAdminFeedback(detail.value.feedback_id)
    uni.showToast({ title: '已软删', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 600)
  } catch (err) {
    const { message } = parseAdminFeedbackError(err)
    uni.showToast({ title: message || '删除失败', icon: 'none' })
    phase.value = 'ready'
  }
}

async function onRestore() {
  if (!detail.value) return
  phase.value = 'saving'
  try {
    detail.value = await restoreAdminFeedback(detail.value.feedback_id)
    uni.showToast({ title: '已恢复', icon: 'success' })
  } catch (err) {
    const { message } = parseAdminFeedbackError(err)
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
  if (!query?.feedback_id) {
    uni.showToast({ title: '参数缺失', icon: 'none' })
    setTimeout(() => uni.navigateBack(), 500)
    return
  }
  await loadDetail(query.feedback_id)
})
</script>

<template>
  <view class="page">
    <view v-if="phase === 'loading'" class="state">
      <text>加载中...</text>
    </view>

    <view v-else-if="detail" class="content">
      <view v-if="detail.is_deleted" class="banner-deleted">
        <text>此反馈已软删. 恢复后才能继续处理.</text>
        <view class="restore-btn" @tap="onRestore">
          <text>恢复</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>用户原文</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">分类</text>
          <text class="meta-val">{{ detail.category }}</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">来源</text>
          <text class="meta-val">{{ detail.platform }}</text>
        </view>
        <view class="meta-row">
          <text class="meta-key">提交</text>
          <text class="meta-val">{{ formatTime(detail.created_at) }}</text>
        </view>
        <view v-if="detail.contact" class="meta-row">
          <text class="meta-key">联系方式</text>
          <text class="meta-val">{{ detail.contact }}</text>
        </view>
        <view v-if="detail.app_version" class="meta-row">
          <text class="meta-key">版本</text>
          <text class="meta-val">{{ detail.app_version }}</text>
        </view>
        <view class="user-content">
          <text>{{ detail.content }}</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>处理状态</text>
        </view>
        <view class="status-chips">
          <view
            v-for="opt in statusOptions"
            :key="opt.value"
            class="chip"
            :class="{
              'chip-active': (detail.admin_status ?? 'pending') === opt.value,
            }"
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
          <text>内部备注 (admin 间)</text>
        </view>
        <textarea
          v-model="noteDraft"
          class="textarea"
          placeholder="留备注给其它 admin 看 (用户看不到; 最长 2000 字)"
          maxlength="2000"
        />
        <view class="save-note-btn" @tap="saveNote">
          <text>保存备注</text>
        </view>
      </view>

      <view v-if="!detail.is_deleted" class="actions">
        <view class="delete-btn" @tap="onDelete">
          <text>软删此反馈</text>
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

.status-chips {
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
  min-height: 200rpx;
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;
  color: #e4e7ee;
  font-size: 26rpx;
  box-sizing: border-box;
  margin-bottom: 16rpx;
}

.save-note-btn {
  padding: 16rpx 32rpx;
  background-color: #3b82f6;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #ffffff;
    font-size: 26rpx;
  }
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
