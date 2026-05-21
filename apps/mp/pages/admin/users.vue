<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * Admin 用户管理列表页 (Sprint 10 FE-S10-002).
 *
 * 路由: ``/pages/admin/users``
 *
 * 功能:
 * 1. 顶部搜索框 (手机/邮箱/昵称 ilike 模糊匹配; 输入 300ms debounce)
 * 2. 筛选 chip: 全部 / 仅管理员 / 含软删用户
 * 3. 列表 item: 头像 + 昵称 + 脱敏手机 + VIP/admin/deleted chip + 创建时间
 * 4. 分页: 下拉刷新 (走第 1 页) + 触底加载下一页
 * 5. 点 item → 跳详情页 ``/pages/admin/users-detail?user_id=xxx``
 *
 * 鉴权:
 * - onLoad 检查 ``authStore.isAdmin``; 非 admin 显示"权限不足"提示 + 跳回我的页
 * - 后端 ``get_current_admin`` 仍做二次校验, 这里是 UX 兜底
 *
 * 不实现 (Sprint 11+ 视需求加):
 * - 高级筛选 (按 VIP 状态 / 按注册时间区间)
 * - 列表排序 (默认按 created_at desc, 后端固定)
 * - 批量操作 (admin 操作量级低, 一次一个足够)
 */

import { onLoad, onPullDownRefresh, onReachBottom } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref, watch } from 'vue'

import {
  listAdminUsers,
  parseAdminUserError,
  type AdminUserListItem,
} from '@/api/admin-users'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

// ─── 列表 state ────────────────────────────────────────────
const items = ref<AdminUserListItem[]>([])
const total = ref<number>(0)
const page = ref<number>(1)
const pageSize = 20

const searchKeyword = ref<string>('')
/** 筛选 chip 状态: all=不过滤 / admin_only=仅 admin / include_deleted=含软删 (与 all 互斥) */
const filterMode = ref<'all' | 'admin_only' | 'include_deleted'>('all')

/** loading 状态: idle (初始) / loading (首次拉) / loading_more (加载更多) / refreshing (下拉) */
const phase = ref<'idle' | 'loading' | 'loading_more' | 'refreshing' | 'empty' | 'error'>(
  'idle',
)

const hasMore = computed(() => items.value.length < total.value)

// ─── helpers ───────────────────────────────────────────────

/**
 * 把筛选 chip 状态翻译成 API query 参数.
 *
 * 注意: ``include_deleted`` 与 ``admin_only`` 互斥并非业务需要 (admin 完全可能想看
 * 软删的 admin), 而是 chip UI 单选简洁; 真要双开就改 multi-select 加状态机.
 */
function buildQuery() {
  return {
    q: searchKeyword.value.trim() || undefined,
    is_admin: filterMode.value === 'admin_only' ? true : undefined,
    include_deleted: filterMode.value === 'include_deleted' ? true : undefined,
    page: page.value,
    page_size: pageSize,
  }
}

function maskedDisplay(item: AdminUserListItem): string {
  // 优先显示昵称, 没昵称用脱敏手机, 都没用脱敏邮箱, 都没显示 "未设置"
  if (item.nickname) return item.nickname
  if (item.phone_masked) return item.phone_masked
  if (item.email_masked) return item.email_masked
  return '未设置'
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  return `${Y}-${M}-${D}`
}

/** VIP 状态 chip 颜色映射. */
function vipChipColor(status: string | null): 'green' | 'amber' | 'gray' {
  if (!status) return 'gray'
  if (status === 'active' || status === 'trialing') return 'green'
  if (status === 'expired' || status === 'cancelled') return 'amber'
  return 'gray'
}

function vipChipLabel(status: string | null): string {
  const map: Record<string, string> = {
    trialing: '试用中',
    active: '已激活',
    expired: '已过期',
    cancelled: '已取消',
  }
  return status ? map[status] ?? status : '无 VIP'
}

// ─── 数据加载 ──────────────────────────────────────────────

async function loadFirst() {
  page.value = 1
  phase.value = 'loading'
  try {
    const resp = await listAdminUsers(buildQuery())
    items.value = resp.items
    total.value = resp.total
    phase.value = resp.items.length === 0 ? 'empty' : 'idle'
  } catch (e) {
    const { code, message } = parseAdminUserError(e)
    console.warn(`[admin-users] loadFirst failed code=${code} message=${message}`)
    phase.value = 'error'
    uni.showToast({ title: code === 'admin_required' ? '权限不足' : '加载失败', icon: 'none' })
    if (code === 'admin_required') {
      setTimeout(() => uni.navigateBack({ delta: 1 }), 800)
    }
  }
}

async function loadMore() {
  if (!hasMore.value || phase.value === 'loading_more') return
  phase.value = 'loading_more'
  try {
    const nextPage = page.value + 1
    const resp = await listAdminUsers({ ...buildQuery(), page: nextPage })
    items.value = [...items.value, ...resp.items]
    total.value = resp.total
    page.value = nextPage
    phase.value = 'idle'
  } catch (e) {
    console.warn('[admin-users] loadMore failed', e)
    phase.value = 'idle' // 不阻塞已渲染的项, toast 提示即可
    uni.showToast({ title: '加载下一页失败', icon: 'none' })
  }
}

async function refresh() {
  phase.value = 'refreshing'
  await loadFirst()
  uni.stopPullDownRefresh()
}

// ─── 交互 ──────────────────────────────────────────────────

// 300ms debounce — 用户敲完最后一字 0.3s 后才真发请求, 避免每键一字打一次.
// 用 watch + setTimeout 模式替代 @input handler — 后者在 uni 端类型签名差异较大,
// v-model 风格在项目其它页 (login/register/profile-complete) 也是默认.
let searchDebounceTimer: ReturnType<typeof setTimeout> | null = null
watch(searchKeyword, () => {
  if (searchDebounceTimer) clearTimeout(searchDebounceTimer)
  searchDebounceTimer = setTimeout(() => {
    void loadFirst()
  }, 300)
})

function onClearSearch() {
  searchKeyword.value = ''
  void loadFirst()
}

function setFilter(mode: 'all' | 'admin_only' | 'include_deleted') {
  if (filterMode.value === mode) return
  filterMode.value = mode
  void loadFirst()
}

function gotoDetail(userId: string) {
  uni.navigateTo({
    url: `/pages/admin/users-detail?user_id=${encodeURIComponent(userId)}`,
  })
}

// ─── lifecycle ─────────────────────────────────────────────

onLoad(() => {
  if (!authStore.loggedIn) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  if (!isAdmin.value) {
    // 防 URL 直接访问非 admin 路径; BE 仍会 403, FE 这层兜底友好
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.navigateBack({ delta: 1 }), 800)
    return
  }
  void loadFirst()
})

onPullDownRefresh(() => {
  void refresh()
})

onReachBottom(() => {
  void loadMore()
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── 搜索 + 筛选 ─── -->
    <view class="filter-bar">
      <view class="search-box">
        <text class="search-icon">🔍</text>
        <input
          v-model="searchKeyword"
          class="search-input"
          placeholder="手机/邮箱/昵称 模糊搜索"
          placeholder-class="search-placeholder"
          maxlength="64"
          confirm-type="search"
        />
        <view
          v-if="searchKeyword"
          class="search-clear"
          hover-class="search-clear-hover"
          :hover-stay-time="80"
          @tap="onClearSearch"
        >
          <text class="search-clear-text">✕</text>
        </view>
      </view>
      <view class="chip-row">
        <view
          :class="['chip', filterMode === 'all' && 'chip-active']"
          hover-class="chip-hover"
          :hover-stay-time="80"
          @tap="setFilter('all')"
        >
          <text class="chip-text">全部</text>
        </view>
        <view
          :class="['chip', filterMode === 'admin_only' && 'chip-active']"
          hover-class="chip-hover"
          :hover-stay-time="80"
          @tap="setFilter('admin_only')"
        >
          <text class="chip-text">仅管理员</text>
        </view>
        <view
          :class="['chip', filterMode === 'include_deleted' && 'chip-active']"
          hover-class="chip-hover"
          :hover-stay-time="80"
          @tap="setFilter('include_deleted')"
        >
          <text class="chip-text">含已删</text>
        </view>
      </view>
    </view>

    <!-- ─── summary 条 ─── -->
    <view class="summary">
      <text class="summary-text">共 {{ total }} 位用户</text>
      <text v-if="searchKeyword" class="summary-q">关键词: {{ searchKeyword }}</text>
    </view>

    <!-- ─── 列表 / 空态 / 错误 ─── -->
    <view v-if="phase === 'loading' && items.length === 0" class="state-block">
      <text class="state-text">加载中…</text>
    </view>

    <view v-else-if="phase === 'error'" class="state-block">
      <text class="state-emoji">😕</text>
      <text class="state-text">加载失败</text>
      <text class="state-sub">网络异常或登录过期, 下拉刷新重试</text>
    </view>

    <view v-else-if="phase === 'empty'" class="state-block">
      <text class="state-emoji">🔍</text>
      <text class="state-text">没找到符合条件的用户</text>
      <text class="state-sub">尝试更换关键词或筛选条件</text>
    </view>

    <view v-else class="user-list">
      <view
        v-for="u in items"
        :key="u.user_id"
        class="user-row"
        hover-class="user-row-hover"
        :hover-stay-time="80"
        @tap="gotoDetail(u.user_id)"
      >
        <image
          v-if="u.avatar_url"
          class="user-avatar"
          :src="u.avatar_url"
          mode="aspectFill"
        />
        <view v-else class="user-avatar user-avatar-placeholder">
          <text class="user-avatar-initial">{{ (u.nickname || u.phone_masked || '?').charAt(0) }}</text>
        </view>
        <view class="user-info">
          <view class="user-head">
            <text class="user-name">{{ maskedDisplay(u) }}</text>
            <view v-if="u.is_admin" class="badge badge-admin">
              <text class="badge-text">管理员</text>
            </view>
            <view v-if="u.is_deleted" class="badge badge-deleted">
              <text class="badge-text">已删</text>
            </view>
          </view>
          <view class="user-meta">
            <text v-if="u.phone_masked && u.phone_masked !== maskedDisplay(u)" class="meta-text">
              {{ u.phone_masked }}
            </text>
            <view :class="['vip-chip', `vip-chip-${vipChipColor(u.vip_status)}`]">
              <text class="vip-chip-text">{{ vipChipLabel(u.vip_status) }}</text>
            </view>
          </view>
          <text class="user-time">注册于 {{ formatDate(u.created_at) }}</text>
        </view>
        <text class="user-arrow">›</text>
      </view>

      <view v-if="phase === 'loading_more'" class="load-more">
        <text class="load-more-text">加载下一页…</text>
      </view>
      <view v-else-if="!hasMore && items.length > 0" class="load-more">
        <text class="load-more-text">— 已加载全部 {{ total }} 位用户 —</text>
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

/* ─── 搜索 + 筛选 ─── */
.filter-bar {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  padding: 8rpx 0 20rpx;
}
.search-box {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 0 20rpx;
  height: 80rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 16rpx;
}
.search-icon {
  font-size: 26rpx;
  color: var(--color-text-muted, #94a3b8);
}
.search-input {
  flex: 1;
  height: 80rpx;
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 80rpx;
}
.search-placeholder {
  color: var(--color-text-muted, #64748b);
  font-size: 26rpx;
}
.search-clear {
  width: 40rpx;
  height: 40rpx;
  line-height: 40rpx;
  text-align: center;
  border-radius: 999rpx;
  background: rgba(255, 255, 255, 0.08);
}
.search-clear-text {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}
.search-clear-hover {
  background: rgba(255, 255, 255, 0.15);
}
.chip-row {
  display: flex;
  gap: 12rpx;
  flex-wrap: wrap;
}
.chip {
  padding: 8rpx 20rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.chip-active {
  background: rgba(167, 139, 250, 0.18);
  border-color: rgba(167, 139, 250, 0.45);
}
.chip-hover {
  background: rgba(255, 255, 255, 0.06);
}
.chip-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}

/* ─── summary ─── */
.summary {
  display: flex;
  align-items: center;
  gap: 12rpx;
  padding: 8rpx 4rpx 16rpx;
}
.summary-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.summary-q {
  font-size: 22rpx;
  color: var(--color-text-muted, #64748b);
  font-style: italic;
}

/* ─── 状态块 ─── */
.state-block {
  margin: 80rpx 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
}
.state-emoji {
  font-size: 64rpx;
}
.state-text {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
.state-sub {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

/* ─── 列表 ─── */
.user-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.user-row {
  display: flex;
  align-items: center;
  gap: 20rpx;
  padding: 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
}
.user-row-hover {
  background: rgba(255, 255, 255, 0.04);
}
.user-avatar {
  width: 80rpx;
  height: 80rpx;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-bg, #0b1220);
}
.user-avatar-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(167, 139, 250, 0.18);
  border: 1rpx solid rgba(167, 139, 250, 0.35);
}
.user-avatar-initial {
  font-size: 32rpx;
  font-weight: 600;
  color: #a78bfa;
}
.user-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  min-width: 0;
}
.user-head {
  display: flex;
  align-items: center;
  gap: 8rpx;
  flex-wrap: wrap;
}
.user-name {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.user-meta {
  display: flex;
  align-items: center;
  gap: 12rpx;
  flex-wrap: wrap;
}
.meta-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.user-time {
  font-size: 20rpx;
  color: var(--color-text-muted, #64748b);
}
.user-arrow {
  font-size: 32rpx;
  color: var(--color-text-muted, #64748b);
  flex-shrink: 0;
}

/* ─── badge / chip ─── */
.badge {
  padding: 2rpx 12rpx;
  border-radius: 999rpx;
  border: 1rpx solid transparent;
}
.badge-admin {
  background: rgba(167, 139, 250, 0.18);
  border-color: rgba(167, 139, 250, 0.4);
}
.badge-deleted {
  background: rgba(148, 163, 184, 0.15);
  border-color: rgba(148, 163, 184, 0.35);
}
.badge-admin .badge-text {
  color: #a78bfa;
}
.badge-deleted .badge-text {
  color: #94a3b8;
}
.badge-text {
  font-size: 20rpx;
}
.vip-chip {
  padding: 2rpx 12rpx;
  border-radius: 999rpx;
  border: 1rpx solid transparent;
}
.vip-chip-green {
  background: rgba(34, 197, 94, 0.15);
  border-color: rgba(34, 197, 94, 0.35);
}
.vip-chip-amber {
  background: rgba(246, 196, 83, 0.15);
  border-color: rgba(246, 196, 83, 0.35);
}
.vip-chip-gray {
  background: rgba(148, 163, 184, 0.12);
  border-color: rgba(148, 163, 184, 0.3);
}
.vip-chip-green .vip-chip-text {
  color: #22c55e;
}
.vip-chip-amber .vip-chip-text {
  color: #f6c453;
}
.vip-chip-gray .vip-chip-text {
  color: #94a3b8;
}
.vip-chip-text {
  font-size: 20rpx;
}

.load-more {
  padding: 24rpx 0;
  text-align: center;
}
.load-more-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
</style>
