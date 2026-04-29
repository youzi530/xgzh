<script setup lang="ts">
/**
 * 社区帖子详情页 (FE-S6-007 接 BE-S6-006/007).
 *
 * 路由: /pages/community/detail?id=<post_id>
 *
 * 模块层次:
 * 1. 帖子主体 (作者头像 / 昵称 / 时间 / 内容 / IPO chip / 状态 chip)
 * 2. 互动 bar (点赞 / 评论 / 举报 / 删除作者私有)
 * 3. 评论列表 (一级 + 折叠的二级)
 * 4. 底部评论输入框 (sticky)
 *
 * 设计要点:
 * - **匿名可读**: BE 列表 / 详情 ``skipAuth: true``; 互动操作未登录拦
 * - **rejected 帖给作者看完整状态 + 拒绝原因**: 别人 → 404
 * - **乐观更新点赞**: 用户点赞瞬时反映 (count +1, icon 切红心), 失败回滚
 * - **举报 modal**: 4 个 reason chip; 命中则 toast "已收到举报, 24h 内审核"
 * - **删除二次确认**
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'

import {
  type CommentDetail,
  type PostDetail,
  type ReportReason,
  createComment,
  createReport,
  deleteComment,
  deletePost,
  getPost,
  listComments,
  parseCommunityError,
  toggleLike,
} from '@/api/community'
import { readAccessTokenSync, useAuthStore } from '@/stores/auth'
import { getNavParam } from '@/utils/navigate'

const post = ref<PostDetail | null>(null)
const comments = ref<CommentDetail[]>([])
const loading = ref(true)
const error = ref('')

const REPORT_OPTIONS: { key: ReportReason; label: string }[] = [
  { key: 'spam', label: '垃圾广告 / 引流' },
  { key: 'illegal', label: '违法违规' },
  { key: 'misleading', label: '虚假误导' },
  { key: 'pornographic', label: '色情低俗' },
  { key: 'privacy', label: '隐私泄露' },
  { key: 'other', label: '其它' },
]

const reportModal = reactive<{ open: boolean; reason: ReportReason | null; detail: string }>({
  open: false,
  reason: null,
  detail: '',
})

const commentInput = ref('')
const submittingComment = ref(false)

const auth = useAuthStore()
const isOwner = computed(() => {
  if (!post.value) return false
  const me = auth.user
  return me !== null && me.user_id === post.value.user_id
})

const isLoggedIn = computed(() => readAccessTokenSync() !== null)

const statusChip = computed(() => {
  const s = post.value?.status
  if (s === 'pending') return { label: '审核中', cls: 'chip-pending' }
  if (s === 'rejected') return { label: '已拒绝', cls: 'chip-rejected' }
  if (s === 'hidden') return { label: '已隐藏', cls: 'chip-rejected' }
  return null
})

async function loadAll(postId: string) {
  loading.value = true
  error.value = ''
  try {
    const [p, cs] = await Promise.all([
      getPost(postId),
      listComments(postId, { page_size: 100 }),
    ])
    post.value = p
    comments.value = cs.items.filter((c) => c.parent_comment_id === null)
  } catch (err) {
    const e = parseCommunityError(err)
    if (e.code === 'not_found') {
      error.value = '帖子不存在或已删除'
    } else {
      error.value = e.message
    }
  } finally {
    loading.value = false
  }
}

function ensureLogin(): boolean {
  if (!isLoggedIn.value) {
    uni.redirectTo({ url: '/pages/auth/login' })
    return false
  }
  return true
}

async function onLikePost() {
  if (!post.value || !ensureLogin()) return
  const target = post.value
  const wasLiked = target.is_liked
  // 乐观更新
  target.is_liked = !wasLiked
  target.likes_count += wasLiked ? -1 : 1
  try {
    const r = await toggleLike('post', target.id)
    target.is_liked = r.liked
    target.likes_count = r.likes_count
  } catch (err) {
    target.is_liked = wasLiked
    target.likes_count += wasLiked ? 1 : -1
    const e = parseCommunityError(err)
    uni.showToast({ title: e.message, icon: 'none' })
  }
}

async function onLikeComment(c: CommentDetail) {
  if (!ensureLogin()) return
  const wasLiked = c.is_liked
  c.is_liked = !wasLiked
  c.likes_count += wasLiked ? -1 : 1
  try {
    const r = await toggleLike('comment', c.id)
    c.is_liked = r.liked
    c.likes_count = r.likes_count
  } catch (err) {
    c.is_liked = wasLiked
    c.likes_count += wasLiked ? 1 : -1
    const e = parseCommunityError(err)
    uni.showToast({ title: e.message, icon: 'none' })
  }
}

async function onSubmitComment() {
  if (!post.value || !ensureLogin()) return
  const content = commentInput.value.trim()
  if (!content) {
    uni.showToast({ title: '请输入内容', icon: 'none' })
    return
  }
  if (content.length > 200) {
    uni.showToast({ title: '内容过长 (>200 字)', icon: 'none' })
    return
  }
  submittingComment.value = true
  try {
    const c = await createComment(post.value.id, { content })
    if (c.status === 'published') {
      comments.value = [c, ...comments.value]
      if (post.value) post.value.comments_count += 1
      commentInput.value = ''
      uni.showToast({ title: '评论已发布', icon: 'success' })
    } else if (c.status === 'pending') {
      uni.showToast({ title: '评论审核中', icon: 'none' })
      commentInput.value = ''
    }
  } catch (err) {
    const e = parseCommunityError(err)
    let title = e.message
    if (e.code === 'too_many_requests') title = '评论过于频繁, 请稍后再试'
    if (e.code === 'content_violation') title = '评论包含违规内容'
    uni.showModal({ title: '评论失败', content: title, showCancel: false })
  } finally {
    submittingComment.value = false
  }
}

async function onDeleteComment(c: CommentDetail) {
  if (!ensureLogin()) return
  uni.showModal({
    title: '确认删除',
    content: '确定要删除这条评论吗?',
    confirmText: '删除',
    confirmColor: '#ef4444',
    success: async (res) => {
      if (!res.confirm) return
      try {
        await deleteComment(c.id)
        comments.value = comments.value.filter((x) => x.id !== c.id)
        if (post.value) post.value.comments_count = Math.max(0, post.value.comments_count - 1)
        uni.showToast({ title: '已删除', icon: 'success' })
      } catch (err) {
        const e = parseCommunityError(err)
        uni.showToast({ title: e.message, icon: 'none' })
      }
    },
  })
}

async function onDeletePost() {
  if (!post.value) return
  uni.showModal({
    title: '确认删除',
    content: '删除后无法恢复, 确定吗?',
    confirmText: '删除',
    confirmColor: '#ef4444',
    success: async (res) => {
      if (!res.confirm || !post.value) return
      try {
        await deletePost(post.value.id)
        uni.showToast({ title: '已删除', icon: 'success' })
        setTimeout(() => uni.navigateBack({ fail: () => {} }), 800)
      } catch (err) {
        const e = parseCommunityError(err)
        uni.showToast({ title: e.message, icon: 'none' })
      }
    },
  })
}

function openReportModal() {
  if (!ensureLogin()) return
  reportModal.open = true
  reportModal.reason = null
  reportModal.detail = ''
}

function closeReportModal() {
  reportModal.open = false
}

function selectReportReason(r: ReportReason) {
  reportModal.reason = r
}

async function onSubmitReport() {
  if (!post.value || !reportModal.reason) {
    uni.showToast({ title: '请选择举报原因', icon: 'none' })
    return
  }
  try {
    await createReport({
      target_type: 'post',
      target_id: post.value.id,
      reason: reportModal.reason,
      detail: reportModal.detail.trim() || undefined,
    })
    uni.showToast({ title: '已收到举报, 24h 内审核', icon: 'success' })
    reportModal.open = false
  } catch (err) {
    const e = parseCommunityError(err)
    let title = e.message
    if (e.code === 'too_many_requests') title = '举报过于频繁'
    uni.showToast({ title, icon: 'none' })
  }
}

function avatarFallback(name: string | null): string {
  return (name || '?').slice(0, 1)
}

function formatTime(iso: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const min = 60_000
  const hour = 60 * min
  const day = 24 * hour
  if (diff < min) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / min)}分钟前`
  if (diff < day) return `${Math.floor(diff / hour)}小时前`
  if (diff < 7 * day) return `${Math.floor(diff / day)}天前`
  const d = new Date(t)
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`
}

function rejectionLabel(r: string): string {
  if (r === 'content_violation') return '违反社区规则'
  if (r === 'spam') return '疑似私域引流'
  if (r === 'privacy_leak') return '隐私泄露'
  return '内容违规'
}

function isCommentOwner(c: CommentDetail): boolean {
  const me = auth.user
  return me !== null && me.user_id === c.user_id
}

onLoad((opts) => {
  const id = getNavParam(opts, 'id')
  if (!id) {
    error.value = '缺少 id 参数'
    loading.value = false
    return
  }
  void loadAll(id)
})
</script>

<template>
  <view class="page">
    <view v-if="loading" class="state-block">
      <text class="state-text">加载中...</text>
    </view>

    <view v-else-if="error" class="state-block">
      <text class="state-emoji">📭</text>
      <text class="state-text">{{ error }}</text>
    </view>

    <template v-else-if="post">
      <view class="content">
        <!-- 帖子主体 -->
        <view class="post-card">
          <view class="post-head">
            <view class="avatar">
              <image
                v-if="post.user_avatar_url"
                class="avatar-img"
                :src="post.user_avatar_url"
                mode="aspectFill"
              />
              <text v-else class="avatar-fallback">{{
                avatarFallback(post.user_nickname)
              }}</text>
            </view>
            <view class="post-meta">
              <text class="post-nickname">{{ post.user_nickname || '匿名用户' }}</text>
              <text class="post-time">{{ formatTime(post.created_at) }}</text>
            </view>
            <text v-if="statusChip" class="status-chip" :class="statusChip.cls">
              {{ statusChip.label }}
            </text>
          </view>

          <view v-if="post.status === 'rejected' && post.rejection_reason" class="reject-reason">
            <text class="reject-text">⚠️ {{ rejectionLabel(post.rejection_reason) }}</text>
            <text class="reject-hint">该帖只对你自己可见</text>
          </view>

          <text class="post-content">{{ post.content }}</text>

          <view v-if="post.related_ipo_code" class="ipo-chip">
            <text class="ipo-chip-text">关联 IPO: {{ post.related_ipo_code }}</text>
          </view>
        </view>

        <!-- 互动 bar -->
        <view class="interaction-bar">
          <view
            class="interaction-btn"
            hover-class="interaction-btn-hover"
            :hover-stay-time="80"
            @tap="onLikePost"
          >
            <text class="interaction-icon">{{ post.is_liked ? '❤️' : '🤍' }}</text>
            <text class="interaction-text">{{ post.likes_count }}</text>
          </view>
          <view class="interaction-btn">
            <text class="interaction-icon">💬</text>
            <text class="interaction-text">{{ post.comments_count }}</text>
          </view>
          <view
            v-if="!isOwner"
            class="interaction-btn"
            hover-class="interaction-btn-hover"
            :hover-stay-time="80"
            @tap="openReportModal"
          >
            <text class="interaction-icon">🚩</text>
            <text class="interaction-text">举报</text>
          </view>
          <view
            v-if="isOwner"
            class="interaction-btn"
            hover-class="interaction-btn-hover"
            :hover-stay-time="80"
            @tap="onDeletePost"
          >
            <text class="interaction-icon">🗑</text>
            <text class="interaction-text-danger">删除</text>
          </view>
        </view>

        <!-- 评论列表 -->
        <view class="comments">
          <view class="comments-head">
            <text class="comments-title">评论 ({{ post.comments_count }})</text>
          </view>
          <view v-if="comments.length === 0" class="comment-empty">
            <text class="comment-empty-text">还没有评论, 来抢沙发吧</text>
          </view>
          <view v-for="c in comments" v-else :key="c.id" class="comment-card">
            <view class="comment-head">
              <view class="comment-avatar">
                <image
                  v-if="c.user_avatar_url"
                  class="avatar-img"
                  :src="c.user_avatar_url"
                  mode="aspectFill"
                />
                <text v-else class="avatar-fallback">{{ avatarFallback(c.user_nickname) }}</text>
              </view>
              <view class="comment-meta">
                <text class="comment-nickname">{{ c.user_nickname || '匿名用户' }}</text>
                <text class="comment-time">{{ formatTime(c.created_at) }}</text>
              </view>
              <text
                v-if="isCommentOwner(c)"
                class="comment-action"
                @tap="onDeleteComment(c)"
              >
                删除
              </text>
            </view>
            <text class="comment-content">{{ c.content }}</text>
            <view class="comment-foot">
              <view
                class="foot-like"
                hover-class="foot-like-hover"
                :hover-stay-time="80"
                @tap="onLikeComment(c)"
              >
                <text class="foot-like-icon">{{ c.is_liked ? '❤️' : '🤍' }}</text>
                <text class="foot-like-text">{{ c.likes_count }}</text>
              </view>
            </view>
          </view>
        </view>
      </view>

      <view class="bottom-spacer" />

      <!-- 评论输入 -->
      <view class="input-bar">
        <input
          v-model="commentInput"
          class="input-field"
          placeholder="说点什么..."
          maxlength="200"
          placeholder-class="input-placeholder"
          confirm-type="send"
          @confirm="onSubmitComment"
        />
        <view
          class="input-send"
          :class="{ 'input-send-disabled': submittingComment || !commentInput.trim() }"
          hover-class="input-send-hover"
          :hover-stay-time="80"
          @tap="onSubmitComment"
        >
          <text class="input-send-text">{{ submittingComment ? '...' : '发送' }}</text>
        </view>
      </view>

      <!-- 举报 modal -->
      <view v-if="reportModal.open" class="modal-mask" @tap="closeReportModal" />
      <view v-if="reportModal.open" class="modal" @tap.stop>
        <view class="modal-head">
          <text class="modal-title">举报</text>
          <text class="modal-close" @tap="closeReportModal">×</text>
        </view>
        <view class="modal-body">
          <text class="modal-label">原因</text>
          <view class="reason-grid">
            <view
              v-for="r in REPORT_OPTIONS"
              :key="r.key"
              class="reason-chip"
              :class="{ 'reason-chip-active': reportModal.reason === r.key }"
              @tap="selectReportReason(r.key)"
            >
              <text class="reason-text">{{ r.label }}</text>
            </view>
          </view>
          <text class="modal-label">详细描述 (可选)</text>
          <textarea
            v-model="reportModal.detail"
            class="modal-textarea"
            placeholder="请补充举报详情, 帮助审核..."
            maxlength="500"
            placeholder-class="input-placeholder"
          />
        </view>
        <view class="modal-foot">
          <view
            class="modal-btn modal-btn-secondary"
            hover-class="modal-btn-secondary-hover"
            :hover-stay-time="80"
            @tap="closeReportModal"
          >
            <text class="modal-btn-text-ghost">取消</text>
          </view>
          <view
            class="modal-btn modal-btn-primary"
            hover-class="modal-btn-primary-hover"
            :hover-stay-time="80"
            @tap="onSubmitReport"
          >
            <text class="modal-btn-text">提交</text>
          </view>
        </view>
      </view>
    </template>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
}
.state-block {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12rpx;
  padding: 200rpx 32rpx;
}
.state-emoji {
  font-size: 80rpx;
}
.state-text {
  font-size: 28rpx;
  color: var(--color-text-muted, #94a3b8);
}

.content {
  padding: 24rpx 32rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}

// ─── post ──────────────────────────────────────────────────
.post-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.post-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
}
.avatar {
  width: 72rpx;
  height: 72rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
}
.avatar-img {
  width: 100%;
  height: 100%;
}
.avatar-fallback {
  color: #fff;
  font-size: 28rpx;
  font-weight: 700;
}
.post-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.post-nickname {
  font-size: 28rpx;
  font-weight: 600;
}
.post-time {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.status-chip {
  font-size: 22rpx;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  font-weight: 600;
}
.chip-pending {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
}
.chip-rejected {
  background: rgba(239, 68, 68, 0.15);
  color: #ef4444;
}

.reject-reason {
  background: rgba(239, 68, 68, 0.08);
  border-left: 4rpx solid #ef4444;
  padding: 16rpx 20rpx;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  border-radius: 8rpx;
}
.reject-text {
  font-size: 24rpx;
  color: #ef4444;
  font-weight: 600;
}
.reject-hint {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.post-content {
  font-size: 30rpx;
  line-height: 1.7;
  word-break: break-all;
  white-space: pre-wrap;
}

.ipo-chip {
  align-self: flex-start;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(246, 196, 83, 0.1);
  border: 1rpx solid rgba(246, 196, 83, 0.3);
}
.ipo-chip-text {
  font-size: 22rpx;
  color: #f6c453;
}

// ─── interaction bar ─────────────────────────────────────
.interaction-bar {
  display: flex;
  flex-direction: row;
  gap: 12rpx;
  padding: 16rpx 8rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 16rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.interaction-btn {
  flex: 1;
  display: flex;
  flex-direction: row;
  align-items: center;
  justify-content: center;
  gap: 10rpx;
  padding: 12rpx 0;
  border-radius: 12rpx;
}
.interaction-btn-hover {
  background: rgba(255, 255, 255, 0.04);
}
.interaction-icon {
  font-size: 28rpx;
}
.interaction-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}
.interaction-text-danger {
  font-size: 24rpx;
  color: #ef4444;
}

// ─── comments ─────────────────────────────────────────────
.comments {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.comments-head {
  padding: 8rpx 4rpx;
}
.comments-title {
  font-size: 26rpx;
  font-weight: 700;
}
.comment-empty {
  padding: 60rpx 24rpx;
  text-align: center;
}
.comment-empty-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.comment-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
  padding: 20rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.comment-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}
.comment-avatar {
  width: 56rpx;
  height: 56rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.comment-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
}
.comment-nickname {
  font-size: 24rpx;
  font-weight: 600;
}
.comment-time {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}
.comment-action {
  font-size: 22rpx;
  color: #ef4444;
  padding: 8rpx 12rpx;
}
.comment-content {
  font-size: 26rpx;
  line-height: 1.6;
  word-break: break-all;
  white-space: pre-wrap;
}
.comment-foot {
  display: flex;
  flex-direction: row;
  justify-content: flex-end;
}
.foot-like {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
}
.foot-like-hover {
  background: rgba(255, 255, 255, 0.04);
}
.foot-like-icon {
  font-size: 22rpx;
}
.foot-like-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

// ─── input bar ────────────────────────────────────────────
.bottom-spacer {
  height: 140rpx;
}
.input-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.98);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  z-index: 50;
}
.input-field {
  flex: 1;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 999rpx;
  padding: 18rpx 24rpx;
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.input-placeholder {
  color: rgba(148, 163, 184, 0.4);
}
.input-send {
  padding: 18rpx 32rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  flex-shrink: 0;
}
.input-send-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.input-send-disabled {
  opacity: 0.5;
}
.input-send-text {
  color: #fff;
  font-size: 26rpx;
  font-weight: 700;
}

// ─── report modal ─────────────────────────────────────────
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  z-index: 200;
}
.modal {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  max-height: 80vh;
  background: var(--color-bg, #0b1220);
  border-top-left-radius: 24rpx;
  border-top-right-radius: 24rpx;
  z-index: 201;
  display: flex;
  flex-direction: column;
}
.modal-head {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
  padding: 24rpx 32rpx;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.06);
}
.modal-title {
  font-size: 30rpx;
  font-weight: 700;
}
.modal-close {
  font-size: 48rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1;
  padding: 0 16rpx;
}
.modal-body {
  padding: 24rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.modal-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.reason-grid {
  display: flex;
  flex-direction: row;
  flex-wrap: wrap;
  gap: 12rpx;
}
.reason-chip {
  padding: 14rpx 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.reason-chip-active {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.5);
}
.reason-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}
.modal-textarea {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  min-height: 120rpx;
  width: 100%;
  box-sizing: border-box;
}
.modal-foot {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  padding: 16rpx 32rpx calc(16rpx + env(safe-area-inset-bottom));
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
}
.modal-btn {
  flex: 1;
  padding: 22rpx 0;
  text-align: center;
  border-radius: 999rpx;
}
.modal-btn-primary {
  background: linear-gradient(135deg, #ef4444, #b91c1c);
}
.modal-btn-primary-hover {
  background: linear-gradient(135deg, #dc2626, #991b1b);
}
.modal-btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}
.modal-btn-secondary-hover {
  background: rgba(255, 255, 255, 0.16);
}
.modal-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}
.modal-btn-text-ghost {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
</style>
