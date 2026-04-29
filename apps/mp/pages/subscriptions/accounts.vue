<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 中签账户管理页 (FE-S6-003 接 BE-S6-002 ``/api/v1/subscriptions/accounts``).
 *
 * 路由: ``/pages/subscriptions/accounts``  (从中签 tab "+ 管理账户" 入口)
 *
 * 模块:
 * 1. 账户列表 (label / broker_name / region / 主账户 chip)
 * 2. 创建账户 modal (label + broker_name + region + is_primary)
 * 3. 编辑账户 modal (复用同 modal, 区别是带 prefilled)
 * 4. 删除账户 (二次确认 + 警告: 级联删 records)
 *
 * 设计要点:
 *
 * - **限流提示**: 60s ≤ 5 次创建, 超出 toast "操作过于频繁"
 * - **主账户互斥**: 设新主账户时, 旧主账户 BE 自动清掉; FE 不本地维护互斥
 * - **删除二次确认**: ``uni.showModal`` 提示"会删除该账户下全部中签记录"
 * - **暗色 token 沿用**
 */

import { onShow } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'

import {
  type SubscriptionAccount,
  type SubscriptionAccountCreateRequest,
  type SubscriptionRegion,
  createAccount,
  deleteAccount,
  listAccounts,
  parseSubscriptionError,
  updateAccount,
} from '@/api/subscription'
import { readAccessTokenSync } from '@/stores/auth'

interface RegionOption {
  key: SubscriptionRegion
  label: string
  emoji: string
}

const REGIONS: RegionOption[] = [
  { key: 'HK', label: '港股', emoji: '🇭🇰' },
  { key: 'CN', label: 'A 股', emoji: '🇨🇳' },
  { key: 'US', label: '美股', emoji: '🇺🇸' },
]

const accounts = ref<SubscriptionAccount[]>([])
const loading = ref(false)
const submitting = ref(false)

const modalOpen = ref(false)
const editingId = ref<string | null>(null)

const form = reactive<{
  label: string
  broker_name: string
  region: SubscriptionRegion
  is_primary: boolean
}>({
  label: '',
  broker_name: '',
  region: 'HK',
  is_primary: false,
})

const formValid = computed(() => {
  const label = form.label.trim()
  return label.length > 0 && label.length <= 32
})

const modalTitle = computed(() => (editingId.value ? '编辑账户' : '创建账户'))

async function refresh() {
  if (readAccessTokenSync() === null) {
    uni.redirectTo({ url: '/pages/auth/login' })
    return
  }
  loading.value = true
  try {
    const res = await listAccounts()
    accounts.value = res.items
  } catch (err) {
    const e = parseSubscriptionError(err)
    if (e.code === 'unauthorized') {
      uni.redirectTo({ url: '/pages/auth/login' })
      return
    }
    uni.showToast({ title: e.message, icon: 'none' })
  } finally {
    loading.value = false
  }
}

onShow(() => {
  void refresh()
})

function openCreate() {
  editingId.value = null
  form.label = ''
  form.broker_name = ''
  form.region = 'HK'
  form.is_primary = accounts.value.length === 0
  modalOpen.value = true
}

function openEdit(acc: SubscriptionAccount) {
  editingId.value = acc.id
  form.label = acc.label
  form.broker_name = acc.broker_name ?? ''
  form.region = acc.region
  form.is_primary = acc.is_primary
  modalOpen.value = true
}

function closeModal() {
  modalOpen.value = false
}

function selectRegion(key: SubscriptionRegion) {
  form.region = key
}

function togglePrimary() {
  form.is_primary = !form.is_primary
}

async function handleSubmit() {
  if (!formValid.value || submitting.value) return
  submitting.value = true
  try {
    if (editingId.value) {
      await updateAccount(editingId.value, {
        label: form.label.trim(),
        broker_name: form.broker_name.trim() || null,
        region: form.region,
        is_primary: form.is_primary,
      })
      uni.showToast({ title: '已保存', icon: 'success' })
    } else {
      const req: SubscriptionAccountCreateRequest = {
        label: form.label.trim(),
        broker_name: form.broker_name.trim() || undefined,
        region: form.region,
        is_primary: form.is_primary,
      }
      await createAccount(req)
      uni.showToast({ title: '已创建', icon: 'success' })
    }
    modalOpen.value = false
    void refresh()
  } catch (err) {
    const e = parseSubscriptionError(err)
    let title = e.message
    if (e.code === 'conflict') title = '账户名已存在'
    if (e.code === 'too_many_requests') title = '操作过于频繁, 请稍后再试'
    uni.showToast({ title, icon: 'none', duration: 2000 })
  } finally {
    submitting.value = false
  }
}

function handleDelete(acc: SubscriptionAccount) {
  uni.showModal({
    title: '确认删除',
    content: `删除账户「${acc.label}」会同时删除该账户下的全部中签记录, 不可恢复. 确认删除?`,
    confirmText: '删除',
    confirmColor: '#ef4444',
    success: async (res) => {
      if (!res.confirm) return
      try {
        await deleteAccount(acc.id)
        uni.showToast({ title: '已删除', icon: 'success' })
        void refresh()
      } catch (err) {
        const e = parseSubscriptionError(err)
        uni.showToast({ title: e.message, icon: 'none' })
      }
    },
  })
}

function regionLabel(r: SubscriptionRegion): string {
  return REGIONS.find((x) => x.key === r)?.label ?? r
}
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view class="hero">
      <text class="hero-title">账户管理</text>
      <text class="hero-subtitle">支持多个券商账户独立记账, 主账户优先汇总</text>
    </view>

    <view v-if="accounts.length === 0 && !loading" class="empty">
      <text class="empty-emoji">📒</text>
      <text class="empty-title">还没有账户</text>
      <text class="empty-desc">添加你常用的券商账户(招商 / 华盛 / 富途...)</text>
    </view>

    <view v-else class="acc-list">
      <view
        v-for="acc in accounts"
        :key="acc.id"
        class="acc-card"
        hover-class="acc-card-hover"
        :hover-stay-time="80"
        @tap="openEdit(acc)"
      >
        <view class="acc-card-head">
          <view class="acc-card-title-row">
            <text v-if="acc.is_primary" class="acc-badge acc-badge-primary">主</text>
            <text class="acc-card-title">{{ acc.label }}</text>
          </view>
          <text class="acc-region">{{ regionLabel(acc.region) }}</text>
        </view>
        <text v-if="acc.broker_name" class="acc-broker">{{ acc.broker_name }}</text>
        <view class="acc-card-foot">
          <text class="acc-action acc-action-edit" @tap.stop="openEdit(acc)">编辑</text>
          <text class="acc-action acc-action-delete" @tap.stop="handleDelete(acc)">删除</text>
        </view>
      </view>
    </view>

    <view class="bottom-spacer" />

    <view class="cta-bar">
      <view
        class="cta-btn"
        hover-class="cta-btn-hover"
        :hover-stay-time="80"
        @tap="openCreate"
      >
        <text class="cta-btn-text">+ 创建账户</text>
      </view>
    </view>

    <!-- modal -->
    <view v-if="modalOpen" class="modal-mask" @tap="closeModal" />
    <view v-if="modalOpen" class="modal" @tap.stop>
      <view class="modal-head">
        <text class="modal-title">{{ modalTitle }}</text>
        <text class="modal-close" @tap="closeModal">×</text>
      </view>
      <scroll-view scroll-y class="modal-body">
        <view class="field">
          <text class="field-label">账户名 *</text>
          <input
            v-model="form.label"
            class="field-input"
            placeholder="例: 招商证券 / 老婆华盛"
            maxlength="32"
            placeholder-class="field-placeholder"
          />
          <text class="field-hint">{{ form.label.trim().length }} / 32</text>
        </view>
        <view class="field">
          <text class="field-label">券商名 (可选)</text>
          <input
            v-model="form.broker_name"
            class="field-input"
            placeholder="例: 招商证券 / 华盛证券"
            maxlength="32"
            placeholder-class="field-placeholder"
          />
        </view>
        <view class="field">
          <text class="field-label">市场</text>
          <view class="region-row">
            <view
              v-for="r in REGIONS"
              :key="r.key"
              class="region-chip"
              :class="{ 'region-chip-active': form.region === r.key }"
              @tap="selectRegion(r.key)"
            >
              <text class="region-chip-emoji">{{ r.emoji }}</text>
              <text class="region-chip-text">{{ r.label }}</text>
            </view>
          </view>
        </view>
        <view class="field">
          <view class="primary-row" @tap="togglePrimary">
            <view class="check-box" :class="{ 'check-box-active': form.is_primary }">
              <text v-if="form.is_primary" class="check-mark">✓</text>
            </view>
            <view class="primary-text">
              <text class="primary-title">设为主账户</text>
              <text class="primary-desc">主账户优先在汇总卡片展示, 同时只能有一个</text>
            </view>
          </view>
        </view>
      </scroll-view>
      <view class="modal-foot">
        <view
          class="modal-btn modal-btn-secondary"
          hover-class="modal-btn-secondary-hover"
          :hover-stay-time="80"
          @tap="closeModal"
        >
          <text class="modal-btn-text-ghost">取消</text>
        </view>
        <view
          class="modal-btn modal-btn-primary"
          :class="{ 'modal-btn-disabled': !formValid || submitting }"
          hover-class="modal-btn-primary-hover"
          :hover-stay-time="80"
          @tap="handleSubmit"
        >
          <text class="modal-btn-text">{{ submitting ? '保存中...' : '保存' }}</text>
        </view>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 32rpx 32rpx 0;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.hero {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.hero-title {
  font-size: 40rpx;
  font-weight: 700;
}
.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding: 80rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  border: 1rpx dashed var(--color-border, rgba(255, 255, 255, 0.1));
}
.empty-emoji {
  font-size: 80rpx;
}
.empty-title {
  font-size: 30rpx;
  font-weight: 600;
}
.empty-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}

.acc-list {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.acc-card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx 28rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.acc-card-hover {
  background: rgba(255, 255, 255, 0.04);
}
.acc-card-head {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}
.acc-card-title-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12rpx;
}
.acc-card-title {
  font-size: 30rpx;
  font-weight: 700;
}
.acc-badge {
  font-size: 22rpx;
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  font-weight: 600;
}
.acc-badge-primary {
  background: rgba(246, 196, 83, 0.15);
  color: #f6c453;
}
.acc-region {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  background: rgba(148, 163, 184, 0.08);
  padding: 4rpx 12rpx;
  border-radius: 6rpx;
}
.acc-broker {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.acc-card-foot {
  display: flex;
  flex-direction: row;
  gap: 24rpx;
  padding-top: 8rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.04);
}
.acc-action {
  font-size: 24rpx;
  padding: 8rpx 16rpx;
}
.acc-action-edit {
  color: #4f8bff;
}
.acc-action-delete {
  color: #ef4444;
}

.bottom-spacer {
  height: 180rpx;
}

.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.95);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  z-index: 50;
}
.cta-btn {
  padding: 22rpx 0;
  text-align: center;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.cta-btn-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.cta-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}

// ─── modal ────────────────────────────────────────────────
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
  flex: 1;
  padding: 24rpx 32rpx;
}

.field {
  margin-bottom: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.field-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.field-input {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 12rpx;
  padding: 20rpx 24rpx;
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
.field-placeholder {
  color: rgba(148, 163, 184, 0.5);
}
.field-hint {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: right;
}

.region-row {
  display: flex;
  flex-direction: row;
  gap: 12rpx;
}
.region-chip {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
  padding: 14rpx 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.region-chip-active {
  background: rgba(79, 139, 255, 0.15);
  border-color: rgba(79, 139, 255, 0.5);
}
.region-chip-emoji {
  font-size: 24rpx;
}
.region-chip-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}

.primary-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
  padding: 16rpx 0;
}
.check-box {
  width: 36rpx;
  height: 36rpx;
  border-radius: 8rpx;
  border: 2rpx solid rgba(255, 255, 255, 0.2);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.check-box-active {
  background: #4f8bff;
  border-color: #4f8bff;
}
.check-mark {
  color: #fff;
  font-size: 26rpx;
  font-weight: 700;
}
.primary-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.primary-title {
  font-size: 26rpx;
  font-weight: 600;
}
.primary-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
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
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.modal-btn-primary-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.modal-btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}
.modal-btn-secondary-hover {
  background: rgba(255, 255, 255, 0.16);
}
.modal-btn-disabled {
  opacity: 0.5;
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
