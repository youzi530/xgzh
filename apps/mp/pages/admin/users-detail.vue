<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * Admin 用户详情 + 操作页 (Sprint 10 FE-S10-002).
 *
 * 路由: ``/pages/admin/users-detail?user_id=xxx``
 *
 * 模块:
 * 1. 用户基础信息 (头像 / 昵称 / 脱敏 phone/email / 邀请码 / 注册时间)
 * 2. 状态信息 (VIP 状态 + 到期时间 + 累计支付 / 邀请人数 / 当前 status / 是否软删)
 * 3. 操作按钮 (5 个, 全部走 modal 二次确认):
 *    - 加 VIP 时长 (输入 days + reason)
 *    - 改昵称 (输入框 maxlength=20)
 *    - 启用/禁用 (status 切换 1 ↔ 0)
 *    - 封禁/解封 (status 切换 1 ↔ -1)  -- 与禁用区分级别: -1 是封号, 0 是临时禁用
 *    - 软删 (红色按钮 + 二次确认; 软删后跳回列表)
 *
 * UX 关键:
 * - 任何写操作都 ``uni.showModal`` 二次确认 — admin 误点成本高
 * - 加 VIP modal 显示"加完后将变为 xxx" 让 admin 看清楚 (非幂等)
 * - 操作成功后 toast + reload 详情, 不跳回列表 (除了软删)
 * - 软删后 nav back + 列表自动刷新 (onShow 应该重拉; 但列表页没 onShow 重拉
 *   的逻辑, 这里靠用户下拉刷新; Sprint 11 可加 onShow auto-refresh)
 */

import { onLoad } from '@dcloudio/uni-app'
import { ref } from 'vue'

import {
  deleteAdminUser,
  getAdminUserDetail,
  grantVipToUser,
  parseAdminUserError,
  updateAdminUser,
  type AdminUserDetail,
} from '@/api/admin-users'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()

const detail = ref<AdminUserDetail | null>(null)
const loading = ref<boolean>(false)
const userId = ref<string>('')

// ─── helpers ──────────────────────────────────────────────


function formatDate(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(iso)
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  const h = String(d.getHours()).padStart(2, '0')
  const m = String(d.getMinutes()).padStart(2, '0')
  return `${Y}-${M}-${D} ${h}:${m}`
}

function daysToDate(days: number): string {
  const target = new Date(Date.now() + days * 24 * 60 * 60 * 1000)
  return formatDate(target.toISOString())
}

function vipChipLabel(status: string | null): string {
  if (!status) return '无 VIP'
  return { trialing: '试用中', active: '已激活', expired: '已过期', cancelled: '已取消' }[status] ?? status
}

function statusLabel(status: number, isDeleted: boolean): string {
  if (isDeleted) return '已软删 (deleted_at)'
  return { 1: '正常', 0: '禁用', [-1]: '封禁' }[status] ?? `未知(${status})`
}

// ─── 数据加载 ──────────────────────────────────────────────

async function loadDetail() {
  if (!userId.value) return
  loading.value = true
  try {
    detail.value = await getAdminUserDetail(userId.value)
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users-detail] load failed code=${code} message=${message}`)
    if (code === 'user_not_found') {
      uni.showToast({ title: '用户不存在', icon: 'none' })
      setTimeout(() => uni.navigateBack({ delta: 1 }), 800)
    } else if (code === 'admin_required') {
      uni.showToast({ title: '权限不足', icon: 'none' })
      setTimeout(() => uni.navigateBack({ delta: 1 }), 800)
    } else {
      uni.showToast({ title: '加载失败', icon: 'none' })
    }
  } finally {
    loading.value = false
  }
}

// ─── 操作: 加 VIP 时长 ───────────────────────────────────

async function actGrantVip() {
  if (!detail.value) return
  // 先输 days
  const daysRes = await uni.showModal({
    title: '加 VIP 时长',
    editable: true,
    placeholderText: '请输入天数 (1-365)',
    confirmText: '下一步',
    cancelText: '取消',
  })
  if (!daysRes.confirm || !daysRes.content) return
  const days = Number(daysRes.content)
  if (!Number.isFinite(days) || days < 1 || days > 365 || !Number.isInteger(days)) {
    uni.showToast({ title: '天数必须是 1-365 的整数', icon: 'none' })
    return
  }
  // 再输 reason
  const reasonRes = await uni.showModal({
    title: '请填理由',
    editable: true,
    placeholderText: '加 VIP 的理由 (2-200 字, 写入审计日志)',
    confirmText: '下一步',
    cancelText: '取消',
  })
  if (!reasonRes.confirm || !reasonRes.content) return
  const reason = reasonRes.content.trim()
  if (reason.length < 2 || reason.length > 200) {
    uni.showToast({ title: '理由 2-200 字', icon: 'none' })
    return
  }
  // 最后二次确认 + 预览到期日
  const currentEnd = detail.value.vip_end_at
  const currentDesc = currentEnd
    ? `当前 VIP 到期: ${formatDate(currentEnd)}`
    : '该用户当前无 VIP (将新建试用券)'
  const previewDate = daysToDate(days)
  const confirmRes = await uni.showModal({
    title: '确认加 VIP',
    content: `${currentDesc}\n加 ${days} 天后约 ${previewDate}\n理由: ${reason}`,
    confirmText: '确认加 VIP',
    confirmColor: '#a78bfa',
    cancelText: '取消',
  })
  if (!confirmRes.confirm) return

  loading.value = true
  try {
    detail.value = await grantVipToUser(userId.value, { days, reason })
    uni.showToast({ title: `已加 ${days} 天`, icon: 'success' })
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users-detail] grantVip failed code=${code} message=${message}`)
    uni.showToast({
      title: code === 'user_not_found' ? '用户已删/不存在' : '操作失败',
      icon: 'none',
    })
  } finally {
    loading.value = false
  }
}

// ─── 操作: 改昵称 ──────────────────────────────────────

async function actChangeNickname() {
  if (!detail.value) return
  const nicknameRes = await uni.showModal({
    title: '改昵称',
    editable: true,
    placeholderText: '请输入新昵称 (1-20 字)',
    content: `当前: ${detail.value.nickname || '(未设置)'}`,
    confirmText: '保存',
    cancelText: '取消',
  })
  if (!nicknameRes.confirm || !nicknameRes.content) return
  const nickname = nicknameRes.content.trim()
  if (!nickname) {
    uni.showToast({ title: '昵称不能为空', icon: 'none' })
    return
  }
  if (nickname.length > 20) {
    uni.showToast({ title: '昵称最长 20 字', icon: 'none' })
    return
  }

  loading.value = true
  try {
    detail.value = await updateAdminUser(userId.value, { nickname })
    uni.showToast({ title: '已修改', icon: 'success' })
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users-detail] changeNickname failed code=${code} message=${message}`)
    uni.showToast({ title: code === 'user_not_found' ? '用户不存在' : '修改失败', icon: 'none' })
  } finally {
    loading.value = false
  }
}

// ─── 操作: 切换 status (启用/禁用/封禁) ─────────────────────

async function actToggleStatus(target: 1 | 0 | -1) {
  if (!detail.value) return
  const map = {
    1: { label: '启用', confirm: '确定要启用该用户?', color: '#22c55e' },
    0: { label: '禁用', confirm: '禁用后用户登录将 401, 确定继续?', color: '#f6c453' },
    [-1]: { label: '封禁', confirm: '封禁=永久禁用, 仅在违规情况使用. 确定继续?', color: '#ef4444' },
  }
  const cfg = map[target]
  const res = await uni.showModal({
    title: cfg.label,
    content: `${cfg.confirm}\n当前状态: ${statusLabel(detail.value.status, detail.value.is_deleted)}`,
    confirmText: cfg.label,
    confirmColor: cfg.color,
    cancelText: '取消',
  })
  if (!res.confirm) return

  loading.value = true
  try {
    detail.value = await updateAdminUser(userId.value, { status: target })
    uni.showToast({ title: `已${cfg.label}`, icon: 'success' })
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users-detail] toggleStatus failed code=${code} message=${message}`)
    uni.showToast({
      title:
        code === 'cannot_demote_self' ? '不能修改自己的状态' :
        code === 'user_not_found' ? '用户不存在' :
        '操作失败',
      icon: 'none',
    })
  } finally {
    loading.value = false
  }
}

// ─── 操作: 软删 ────────────────────────────────────────

async function actSoftDelete() {
  if (!detail.value) return
  const res = await uni.showModal({
    title: '软删用户',
    content: `确定软删该用户? 操作后:\n- 用户立即下线 (refresh 全拉黑)\n- 邀请码失效\n- 30天后系统硬删 PII\n该操作不可撤销.`,
    confirmText: '确认软删',
    confirmColor: '#ef4444',
    cancelText: '取消',
  })
  if (!res.confirm) return

  loading.value = true
  try {
    await deleteAdminUser(userId.value)
    uni.showToast({ title: '已软删', icon: 'success' })
    // 软删成功跳回列表 — 详情页对一个已删用户继续操作意义不大
    setTimeout(() => uni.navigateBack({ delta: 1 }), 600)
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users-detail] softDelete failed code=${code} message=${message}`)
    uni.showToast({
      title:
        code === 'cannot_delete_self' ? '不能删除自己' :
        code === 'user_not_found' ? '用户不存在' :
        '操作失败',
      icon: 'none',
    })
    loading.value = false
  }
}

// ─── lifecycle ─────────────────────────────────────────────

onLoad((options) => {
  if (!authStore.loggedIn) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  if (!authStore.isAdmin) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.navigateBack({ delta: 1 }), 800)
    return
  }
  const uid = options?.user_id as string | undefined
  if (!uid) {
    uni.showToast({ title: '缺少 user_id', icon: 'none' })
    setTimeout(() => uni.navigateBack({ delta: 1 }), 600)
    return
  }
  userId.value = uid
  void loadDetail()
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view v-if="loading && !detail" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="detail" class="content">
      <!-- ─── 用户基础信息 ─── -->
      <view class="user-card">
        <image
          v-if="detail.avatar_url"
          class="user-avatar"
          :src="detail.avatar_url"
          mode="aspectFill"
        />
        <view v-else class="user-avatar user-avatar-placeholder">
          <text class="user-avatar-initial">{{ (detail.nickname || detail.phone_masked || '?').charAt(0) }}</text>
        </view>
        <view class="user-info">
          <view class="user-head">
            <text class="user-name">{{ detail.nickname || '(未设置昵称)' }}</text>
            <view v-if="detail.is_admin" class="badge badge-admin">
              <text class="badge-text">管理员</text>
            </view>
            <view v-if="detail.is_deleted" class="badge badge-deleted">
              <text class="badge-text">已软删</text>
            </view>
          </view>
          <text class="user-id">ID: {{ detail.user_id.slice(0, 8) }}…</text>
        </view>
      </view>

      <!-- ─── 详细字段 ─── -->
      <view class="field-block">
        <view class="field-row">
          <text class="field-label">手机</text>
          <text class="field-value">{{ detail.phone_masked || '—' }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">邮箱</text>
          <text class="field-value">{{ detail.email_masked || '—' }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">地区</text>
          <text class="field-value">{{ detail.region }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">邀请码</text>
          <text class="field-value">{{ detail.invite_code }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">邀请人数</text>
          <text class="field-value">{{ detail.invite_count }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">注册时间</text>
          <text class="field-value">{{ formatDate(detail.created_at) }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">最后活跃</text>
          <text class="field-value">{{ formatDate(detail.last_active_at) }}</text>
        </view>
        <view class="field-row">
          <text class="field-label">账户状态</text>
          <text class="field-value">{{ statusLabel(detail.status, detail.is_deleted) }}</text>
        </view>
        <view v-if="detail.deleted_at" class="field-row">
          <text class="field-label">软删时间</text>
          <text class="field-value">{{ formatDate(detail.deleted_at) }}</text>
        </view>
      </view>

      <!-- ─── VIP 信息 ─── -->
      <view class="field-block">
        <view class="field-block-title">
          <text class="field-block-title-text">VIP 订阅</text>
        </view>
        <view class="field-row">
          <text class="field-label">状态</text>
          <text class="field-value">{{ vipChipLabel(detail.vip_status) }}</text>
        </view>
        <view v-if="detail.vip_plan" class="field-row">
          <text class="field-label">套餐</text>
          <text class="field-value">{{ detail.vip_plan }}</text>
        </view>
        <view v-if="detail.vip_start_at" class="field-row">
          <text class="field-label">开始</text>
          <text class="field-value">{{ formatDate(detail.vip_start_at) }}</text>
        </view>
        <view v-if="detail.vip_end_at" class="field-row">
          <text class="field-label">到期</text>
          <text class="field-value">{{ formatDate(detail.vip_end_at) }}</text>
        </view>
        <view v-if="detail.vip_total_paid_cny !== null" class="field-row">
          <text class="field-label">累计支付</text>
          <text class="field-value">¥ {{ detail.vip_total_paid_cny }}</text>
        </view>
      </view>

      <!-- ─── 操作按钮 ─── -->
      <view v-if="!detail.is_deleted" class="action-block">
        <view class="action-block-title">
          <text class="action-block-title-text">操作</text>
        </view>
        <view
          class="action-btn action-btn-primary"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actGrantVip"
        >
          <text class="action-btn-text">加 VIP 时长</text>
        </view>
        <view
          class="action-btn"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actChangeNickname"
        >
          <text class="action-btn-text">改昵称</text>
        </view>
        <view
          v-if="detail.status !== 1"
          class="action-btn action-btn-positive"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actToggleStatus(1)"
        >
          <text class="action-btn-text">启用账号</text>
        </view>
        <view
          v-if="detail.status === 1"
          class="action-btn action-btn-warning"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actToggleStatus(0)"
        >
          <text class="action-btn-text">禁用账号 (临时)</text>
        </view>
        <view
          v-if="detail.status !== -1"
          class="action-btn action-btn-danger"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actToggleStatus(-1)"
        >
          <text class="action-btn-text">封禁账号 (违规)</text>
        </view>
        <view
          class="action-btn action-btn-danger"
          hover-class="action-btn-hover"
          :hover-stay-time="80"
          @tap="actSoftDelete"
        >
          <text class="action-btn-text">软删用户</text>
        </view>
      </view>

      <view v-else class="deleted-notice">
        <text class="deleted-notice-text">
          该用户已软删 ({{ formatDate(detail.deleted_at) }}); 30 天后系统将硬删 PII.
          软删后不再支持操作 (PIPL §47).
        </text>
      </view>
    </view>
  </view>
</template>

<style scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background: var(--color-bg, #0b1220);
  padding-bottom: 40rpx;
}

.state-block {
  margin: 80rpx 0;
  text-align: center;
}
.state-text {
  font-size: 28rpx;
  color: var(--color-text-muted, #94a3b8);
}

.content {
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

/* ─── 用户卡片 ─── */
.user-card {
  display: flex;
  align-items: center;
  gap: 24rpx;
  padding: 32rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
}
.user-avatar {
  width: 120rpx;
  height: 120rpx;
  border-radius: 50%;
  flex-shrink: 0;
}
.user-avatar-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(167, 139, 250, 0.18);
  border: 1rpx solid rgba(167, 139, 250, 0.35);
}
.user-avatar-initial {
  font-size: 48rpx;
  font-weight: 600;
  color: #a78bfa;
}
.user-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
  min-width: 0;
}
.user-head {
  display: flex;
  align-items: center;
  gap: 12rpx;
  flex-wrap: wrap;
}
.user-name {
  font-size: 32rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.user-id {
  font-size: 22rpx;
  color: var(--color-text-muted, #64748b);
  font-family: 'SF Mono', Menlo, monospace;
}

/* ─── 字段区块 ─── */
.field-block {
  padding: 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
}
.field-block-title {
  padding-bottom: 12rpx;
  margin-bottom: 12rpx;
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.field-block-title-text {
  font-size: 26rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.field-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12rpx 0;
  gap: 24rpx;
}
.field-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  flex-shrink: 0;
}
.field-value {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
  text-align: right;
  word-break: break-all;
}

/* ─── 操作区块 ─── */
.action-block {
  padding: 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.action-block-title {
  padding-bottom: 12rpx;
  margin-bottom: 4rpx;
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.action-block-title-text {
  font-size: 26rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.action-btn {
  padding: 24rpx;
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.1));
  border-radius: 16rpx;
  text-align: center;
}
.action-btn-text {
  font-size: 28rpx;
  font-weight: 500;
  color: var(--color-text, #e2e8f0);
}
.action-btn-primary {
  background: rgba(167, 139, 250, 0.18);
  border-color: rgba(167, 139, 250, 0.4);
}
.action-btn-primary .action-btn-text {
  color: #a78bfa;
}
.action-btn-positive {
  background: rgba(34, 197, 94, 0.15);
  border-color: rgba(34, 197, 94, 0.4);
}
.action-btn-positive .action-btn-text {
  color: #22c55e;
}
.action-btn-warning {
  background: rgba(246, 196, 83, 0.15);
  border-color: rgba(246, 196, 83, 0.4);
}
.action-btn-warning .action-btn-text {
  color: #f6c453;
}
.action-btn-danger {
  background: rgba(239, 68, 68, 0.15);
  border-color: rgba(239, 68, 68, 0.4);
}
.action-btn-danger .action-btn-text {
  color: #ef4444;
}
.action-btn-hover {
  opacity: 0.7;
}

/* ─── deleted notice ─── */
.deleted-notice {
  padding: 24rpx;
  background: rgba(148, 163, 184, 0.1);
  border: 1rpx solid rgba(148, 163, 184, 0.3);
  border-radius: 16rpx;
}
.deleted-notice-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
}

/* ─── badge ─── */
.badge {
  padding: 2rpx 12rpx;
  border-radius: 999rpx;
}
.badge-admin {
  background: rgba(167, 139, 250, 0.18);
  border: 1rpx solid rgba(167, 139, 250, 0.4);
}
.badge-deleted {
  background: rgba(148, 163, 184, 0.15);
  border: 1rpx solid rgba(148, 163, 184, 0.35);
}
.badge-admin .badge-text {
  color: #a78bfa;
  font-size: 20rpx;
}
.badge-deleted .badge-text {
  color: #94a3b8;
  font-size: 20rpx;
}
</style>
