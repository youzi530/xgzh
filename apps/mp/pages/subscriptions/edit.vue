<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 中签录入 / 编辑表单 (FE-S6-003 接 BE-S6-002 ``POST/PUT /api/v1/subscriptions``).
 *
 * 路由:
 * - 新建: ``/pages/subscriptions/edit``
 * - 编辑: ``/pages/subscriptions/edit?id=<record_id>``
 *
 * 字段 (与 BE-S6-001 schema 对齐):
 * - **必填**: account_id / ipo_code / region / subscribe_shares / subscribed_at
 * - **可选**: ipo_name / allotted_shares / subscribe_price / margin_amount / fees /
 *            first_day_close / sell_price / sell_at / notes / listed_at
 *
 * 设计要点:
 *
 * - **金额传字符串**: 后端 Decimal 精度敏感, FE 用 ``string`` 透传, 不做 ``Number``
 *   解析; 实时预览的 PnL 估算用 ``parseFloat`` 仅展示, 不影响入库
 *
 * - **失败保留输入**: 用户精心填的 10 个字段不能因为网络/校验抖动被清; 提交失败
 *   仅 toast 报错, 表单状态不动
 *
 * - **PnL 实时预览**: ``unrealized = (first_day_close - subscribe_price) * allotted - fees - margin``
 *   ``realized = (sell_price - subscribe_price) * allotted - fees - margin``
 *   完全前端推算 (BE 也会再算一次落库); 字段未填时显 "—"
 *
 * - **市场跟随账户**: 选账户时自动联动 region (HK/CN/US), 用户也可手动改
 *
 * - **没账户时拦下**: ``onShow`` 拉账户列表, 0 账户时显引导块跳 accounts 页
 *
 * - **删除按钮 (仅编辑态)**: 点 → 二次确认 → 调 deleteRecord → 回中签主页
 */

import { onLoad, onShow } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'

import {
  type SubscriptionAccount,
  type SubscriptionRecord,
  type SubscriptionRecordCreateRequest,
  type SubscriptionRegion,
  createRecord,
  deleteRecord,
  getRecord,
  listAccounts,
  parseSubscriptionError,
  updateRecord,
} from '@/api/subscription'
import { readAccessTokenSync } from '@/stores/auth'
import { getNavParam } from '@/utils/navigate'

interface RegionOption {
  key: SubscriptionRegion
  label: string
}
const REGIONS: RegionOption[] = [
  { key: 'HK', label: '港股' },
  { key: 'CN', label: 'A 股' },
  { key: 'US', label: '美股' },
]

const recordId = ref<string | null>(null)
const isEdit = computed(() => recordId.value !== null)
const accounts = ref<SubscriptionAccount[]>([])
const loading = ref(false)
const submitting = ref(false)

interface FormState {
  account_id: string
  ipo_code: string
  ipo_name: string
  region: SubscriptionRegion
  subscribe_shares: string
  allotted_shares: string
  subscribe_price: string
  margin_amount: string
  fees: string
  first_day_close: string
  sell_price: string
  sell_at: string
  subscribed_at: string
  listed_at: string
  notes: string
}

const form = reactive<FormState>({
  account_id: '',
  ipo_code: '',
  ipo_name: '',
  region: 'HK',
  subscribe_shares: '',
  allotted_shares: '',
  subscribe_price: '',
  margin_amount: '',
  fees: '',
  first_day_close: '',
  sell_price: '',
  sell_at: '',
  subscribed_at: '',
  listed_at: '',
  notes: '',
})

function todayStr(): string {
  const d = new Date()
  const Y = d.getFullYear()
  const M = String(d.getMonth() + 1).padStart(2, '0')
  const D = String(d.getDate()).padStart(2, '0')
  return `${Y}-${M}-${D}`
}

const accountOptions = computed(() =>
  accounts.value.map((a) => ({
    value: a.id,
    label: a.is_primary ? `★ ${a.label}` : a.label,
  })),
)

const accountIndex = computed(() => {
  const idx = accounts.value.findIndex((a) => a.id === form.account_id)
  return idx < 0 ? 0 : idx
})

const regionIndex = computed(() => {
  const idx = REGIONS.findIndex((r) => r.key === form.region)
  return idx < 0 ? 0 : idx
})

const validation = computed(() => {
  const errs: string[] = []
  if (!form.account_id) errs.push('请选择账户')
  if (!form.ipo_code.trim()) errs.push('请填写代码')
  if (!form.subscribe_shares) errs.push('请填写申购股数')
  else if (!/^\d+$/.test(form.subscribe_shares) || Number(form.subscribe_shares) <= 0) {
    errs.push('申购股数应为正整数')
  }
  if (form.allotted_shares) {
    if (!/^\d+$/.test(form.allotted_shares)) errs.push('中签股数应为非负整数')
    else if (
      form.subscribe_shares &&
      Number(form.allotted_shares) > Number(form.subscribe_shares)
    ) {
      errs.push('中签股数不能超过申购股数')
    }
  }
  if (!form.subscribed_at) errs.push('请选择申购日')
  return errs
})

const formValid = computed(() => validation.value.length === 0)

const previewUnrealized = computed(() => {
  const close = parseFloat(form.first_day_close)
  const price = parseFloat(form.subscribe_price)
  const allotted = parseFloat(form.allotted_shares || '0')
  const fees = parseFloat(form.fees || '0')
  const margin = parseFloat(form.margin_amount || '0')
  if (Number.isNaN(close) || Number.isNaN(price) || allotted <= 0) return null
  return (close - price) * allotted - (Number.isNaN(fees) ? 0 : fees) - (Number.isNaN(margin) ? 0 : margin)
})

const previewRealized = computed(() => {
  const sell = parseFloat(form.sell_price)
  const price = parseFloat(form.subscribe_price)
  const allotted = parseFloat(form.allotted_shares || '0')
  const fees = parseFloat(form.fees || '0')
  const margin = parseFloat(form.margin_amount || '0')
  if (Number.isNaN(sell) || Number.isNaN(price) || allotted <= 0) return null
  return (sell - price) * allotted - (Number.isNaN(fees) ? 0 : fees) - (Number.isNaN(margin) ? 0 : margin)
})

function fmtPnL(n: number | null): string {
  if (n === null) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toFixed(2)}`
}

function pnlClass(n: number | null): string {
  if (n === null) return 'preview-neutral'
  if (n > 0) return 'preview-up'
  if (n < 0) return 'preview-down'
  return 'preview-neutral'
}

function onAccountChange(e: { detail: { value: string | number } }) {
  const idx = Number(e.detail.value)
  const acc = accounts.value[idx]
  if (acc) {
    form.account_id = acc.id
    form.region = acc.region
  }
}

function onRegionChange(e: { detail: { value: string | number } }) {
  const idx = Number(e.detail.value)
  const r = REGIONS[idx]
  if (r) form.region = r.key
}

function onDateChange(field: 'subscribed_at' | 'listed_at' | 'sell_at') {
  return (e: { detail: { value: string } }) => {
    form[field] = e.detail.value
  }
}

async function loadAccounts() {
  if (readAccessTokenSync() === null) {
    uni.redirectTo({ url: '/pages/auth/login' })
    return
  }
  try {
    const res = await listAccounts()
    accounts.value = res.items
    if (!form.account_id && accounts.value.length > 0) {
      const primary = accounts.value.find((a) => a.is_primary) ?? accounts.value[0]
      form.account_id = primary.id
      if (!isEdit.value) form.region = primary.region
    }
  } catch (err) {
    const e = parseSubscriptionError(err)
    if (e.code === 'unauthorized') {
      uni.redirectTo({ url: '/pages/auth/login' })
      return
    }
    uni.showToast({ title: e.message, icon: 'none' })
  }
}

async function loadRecord(id: string) {
  loading.value = true
  try {
    const r = await getRecord(id)
    fillFromRecord(r)
  } catch (err) {
    const e = parseSubscriptionError(err)
    uni.showToast({ title: e.message || '记录不存在', icon: 'none' })
    setTimeout(() => uni.navigateBack({ fail: () => {} }), 800)
  } finally {
    loading.value = false
  }
}

function fillFromRecord(r: SubscriptionRecord) {
  form.account_id = r.account_id
  form.ipo_code = r.ipo_code
  form.ipo_name = r.ipo_name ?? ''
  form.region = r.region
  form.subscribe_shares = String(r.subscribe_shares)
  form.allotted_shares = r.allotted_shares > 0 ? String(r.allotted_shares) : ''
  form.subscribe_price = r.subscribe_price ?? ''
  form.margin_amount = r.margin_amount ?? ''
  form.fees = r.fees
  form.first_day_close = r.first_day_close ?? ''
  form.sell_price = r.sell_price ?? ''
  form.sell_at = r.sell_at ? r.sell_at.slice(0, 10) : ''
  form.subscribed_at = r.subscribed_at
  form.listed_at = r.listed_at ?? ''
  form.notes = r.notes ?? ''
}

function buildPayload(): SubscriptionRecordCreateRequest {
  const pickStr = (s: string) => (s.trim() === '' ? undefined : s.trim())
  return {
    account_id: form.account_id,
    ipo_code: form.ipo_code.trim().toUpperCase(),
    ipo_name: pickStr(form.ipo_name),
    region: form.region,
    subscribe_shares: Number(form.subscribe_shares),
    allotted_shares: form.allotted_shares ? Number(form.allotted_shares) : 0,
    subscribe_price: pickStr(form.subscribe_price),
    margin_amount: pickStr(form.margin_amount),
    fees: pickStr(form.fees),
    first_day_close: pickStr(form.first_day_close),
    sell_price: pickStr(form.sell_price),
    sell_at: form.sell_at ? `${form.sell_at}T00:00:00Z` : undefined,
    subscribed_at: form.subscribed_at,
    listed_at: pickStr(form.listed_at),
    notes: pickStr(form.notes),
  }
}

async function handleSubmit() {
  if (!formValid.value || submitting.value) {
    if (validation.value.length > 0) {
      uni.showToast({ title: validation.value[0], icon: 'none' })
    }
    return
  }
  submitting.value = true
  const payload = buildPayload()
  try {
    if (isEdit.value && recordId.value) {
      await updateRecord(recordId.value, payload)
    } else {
      await createRecord(payload)
    }
    uni.showToast({ title: isEdit.value ? '已保存' : '已录入', icon: 'success', duration: 1200 })
    setTimeout(() => {
      uni.navigateBack({
        fail: () =>
          uni.switchTab({
            url: '/pages/subscriptions/index',
            fail: () => uni.reLaunch({ url: '/pages/subscriptions/index' }),
          }),
      })
    }, 1000)
  } catch (err) {
    const e = parseSubscriptionError(err)
    let title = e.message
    if (e.code === 'too_many_requests') title = '操作过于频繁, 请稍后再试'
    if (e.code === 'invalid_field') title = e.message || '字段校验失败'
    uni.showToast({ title, icon: 'none', duration: 2000 })
  } finally {
    submitting.value = false
  }
}

function handleDelete() {
  if (!recordId.value) return
  uni.showModal({
    title: '确认删除',
    content: '删除后无法恢复, 确认删除这条中签记录?',
    confirmText: '删除',
    confirmColor: '#ef4444',
    success: async (res) => {
      if (!res.confirm || !recordId.value) return
      try {
        await deleteRecord(recordId.value)
        uni.showToast({ title: '已删除', icon: 'success' })
        setTimeout(() => uni.navigateBack({ fail: () => {} }), 800)
      } catch (err) {
        const e = parseSubscriptionError(err)
        uni.showToast({ title: e.message, icon: 'none' })
      }
    },
  })
}

function gotoAccountManage() {
  uni.navigateTo({ url: '/pages/subscriptions/accounts' })
}

onLoad((opts) => {
  const id = getNavParam(opts, 'id')
  if (id) {
    recordId.value = id
    void loadRecord(id)
    uni.setNavigationBarTitle({ title: '编辑中签' })
  } else {
    form.subscribed_at = todayStr()
  }
})

onShow(() => {
  void loadAccounts()
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view v-if="loading" class="state-block">
      <text class="state-text">加载中...</text>
    </view>

    <template v-else>
      <view v-if="accounts.length === 0" class="empty">
        <text class="empty-emoji">📒</text>
        <text class="empty-title">需要先创建账户</text>
        <text class="empty-desc">中签记录必须挂在某个券商账户下</text>
        <view
          class="empty-btn"
          hover-class="empty-btn-hover"
          :hover-stay-time="80"
          @tap="gotoAccountManage"
        >
          <text class="empty-btn-text">+ 创建账户</text>
        </view>
      </view>

      <view v-else class="form">
        <!-- 基本信息 -->
        <view class="section">
          <text class="section-title">基本信息</text>

          <view class="field">
            <text class="field-label">账户 *</text>
            <picker
              :value="accountIndex"
              :range="accountOptions"
              range-key="label"
              @change="onAccountChange"
            >
              <view class="picker-display">
                <text class="picker-text">{{
                  accountOptions[accountIndex]?.label || '请选择'
                }}</text>
                <text class="picker-arrow">▾</text>
              </view>
            </picker>
          </view>

          <view class="field">
            <text class="field-label">市场</text>
            <picker :value="regionIndex" :range="REGIONS" range-key="label" @change="onRegionChange">
              <view class="picker-display">
                <text class="picker-text">{{ REGIONS[regionIndex].label }}</text>
                <text class="picker-arrow">▾</text>
              </view>
            </picker>
          </view>

          <view class="field">
            <text class="field-label">代码 *</text>
            <input
              v-model="form.ipo_code"
              class="field-input"
              placeholder="例: 00700 / 688123"
              maxlength="16"
              placeholder-class="field-placeholder"
            />
          </view>

          <view class="field">
            <text class="field-label">名称 (可选)</text>
            <input
              v-model="form.ipo_name"
              class="field-input"
              placeholder="例: 腾讯控股"
              maxlength="64"
              placeholder-class="field-placeholder"
            />
          </view>
        </view>

        <!-- 申购数据 -->
        <view class="section">
          <text class="section-title">申购数据</text>
          <view class="row-2">
            <view class="field">
              <text class="field-label">申购股数 *</text>
              <input
                v-model="form.subscribe_shares"
                class="field-input"
                type="number"
                placeholder="0"
                placeholder-class="field-placeholder"
              />
            </view>
            <view class="field">
              <text class="field-label">中签股数</text>
              <input
                v-model="form.allotted_shares"
                class="field-input"
                type="number"
                placeholder="0 = 未中"
                placeholder-class="field-placeholder"
              />
            </view>
          </view>
          <view class="row-2">
            <view class="field">
              <text class="field-label">招股价</text>
              <input
                v-model="form.subscribe_price"
                class="field-input"
                type="digit"
                placeholder="例: 30.50"
                placeholder-class="field-placeholder"
              />
            </view>
            <view class="field">
              <text class="field-label">手续费</text>
              <input
                v-model="form.fees"
                class="field-input"
                type="digit"
                placeholder="0"
                placeholder-class="field-placeholder"
              />
            </view>
          </view>
          <view v-if="form.region === 'HK'" class="field">
            <text class="field-label">孖展利息 (港股专属)</text>
            <input
              v-model="form.margin_amount"
              class="field-input"
              type="digit"
              placeholder="0"
              placeholder-class="field-placeholder"
            />
          </view>
          <view class="field">
            <text class="field-label">申购日 *</text>
            <picker
              mode="date"
              :value="form.subscribed_at"
              @change="onDateChange('subscribed_at')"
            >
              <view class="picker-display">
                <text class="picker-text">{{ form.subscribed_at || '请选择' }}</text>
                <text class="picker-arrow">▾</text>
              </view>
            </picker>
          </view>
        </view>

        <!-- 上市与卖出 -->
        <view class="section">
          <text class="section-title">上市与卖出 (可选)</text>
          <view class="row-2">
            <view class="field">
              <text class="field-label">上市日</text>
              <picker mode="date" :value="form.listed_at" @change="onDateChange('listed_at')">
                <view class="picker-display">
                  <text class="picker-text">{{ form.listed_at || '请选择' }}</text>
                  <text class="picker-arrow">▾</text>
                </view>
              </picker>
            </view>
            <view class="field">
              <text class="field-label">首日收盘</text>
              <input
                v-model="form.first_day_close"
                class="field-input"
                type="digit"
                placeholder="例: 32.40"
                placeholder-class="field-placeholder"
              />
            </view>
          </view>
          <view class="row-2">
            <view class="field">
              <text class="field-label">卖出价</text>
              <input
                v-model="form.sell_price"
                class="field-input"
                type="digit"
                placeholder="还持有则留空"
                placeholder-class="field-placeholder"
              />
            </view>
            <view class="field">
              <text class="field-label">卖出日</text>
              <picker mode="date" :value="form.sell_at" @change="onDateChange('sell_at')">
                <view class="picker-display">
                  <text class="picker-text">{{ form.sell_at || '请选择' }}</text>
                  <text class="picker-arrow">▾</text>
                </view>
              </picker>
            </view>
          </view>
        </view>

        <!-- 备注 -->
        <view class="section">
          <text class="section-title">备注</text>
          <view class="field">
            <textarea
              v-model="form.notes"
              class="field-textarea"
              placeholder="例: 暗盘破发, 首日割肉"
              maxlength="500"
              placeholder-class="field-placeholder"
            />
            <text class="field-hint">{{ form.notes.length }} / 500</text>
          </view>
        </view>

        <!-- PnL 实时预览 -->
        <view class="preview">
          <text class="preview-title">P&amp;L 预览</text>
          <view class="preview-row">
            <text class="preview-label">浮盈 (按首日)</text>
            <text class="preview-value" :class="pnlClass(previewUnrealized)">
              {{ fmtPnL(previewUnrealized) }}
            </text>
          </view>
          <view class="preview-row">
            <text class="preview-label">已实现 (按卖出)</text>
            <text class="preview-value" :class="pnlClass(previewRealized)">
              {{ fmtPnL(previewRealized) }}
            </text>
          </view>
          <text class="preview-hint">仅前端估算, 后端会以最终落库为准</text>
        </view>

        <view class="bottom-spacer" />

        <!-- 底部 CTA -->
        <view class="cta-bar">
          <view
            v-if="isEdit"
            class="cta-btn cta-btn-danger"
            hover-class="cta-btn-danger-hover"
            :hover-stay-time="80"
            @tap="handleDelete"
          >
            <text class="cta-btn-text-danger">删除</text>
          </view>
          <view
            class="cta-btn cta-btn-primary"
            :class="{ 'cta-btn-disabled': !formValid || submitting }"
            hover-class="cta-btn-primary-hover"
            :hover-stay-time="80"
            @tap="handleSubmit"
          >
            <text class="cta-btn-text">{{
              submitting ? '提交中...' : isEdit ? '保存' : '录入'
            }}</text>
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
  align-items: center;
  justify-content: center;
  padding: 200rpx 32rpx;
}
.state-text {
  font-size: 28rpx;
  color: var(--color-text-muted, #94a3b8);
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 16rpx;
  margin: 80rpx 32rpx;
  padding: 80rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  border: 1rpx dashed rgba(255, 255, 255, 0.1);
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
.empty-btn {
  margin-top: 16rpx;
  padding: 22rpx 48rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.empty-btn-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.empty-btn-text {
  font-size: 26rpx;
  color: #fff;
  font-weight: 700;
}

.form {
  padding: 24rpx 32rpx;
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.section {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.section-title {
  font-size: 26rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  padding-bottom: 8rpx;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.04);
}

.row-2 {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
}
.row-2 .field {
  flex: 1;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.field-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.field-input {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.field-textarea {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
  width: 100%;
  min-height: 120rpx;
  box-sizing: border-box;
}
.field-placeholder {
  color: rgba(148, 163, 184, 0.4);
}
.field-hint {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: right;
}

.picker-display {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}
.picker-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.picker-arrow {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.preview {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.06), rgba(79, 139, 255, 0.04));
  border: 1rpx solid rgba(246, 196, 83, 0.3);
  border-radius: 20rpx;
  padding: 20rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 12rpx;
}
.preview-title {
  font-size: 24rpx;
  font-weight: 700;
  color: #f6c453;
}
.preview-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}
.preview-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.preview-value {
  font-size: 28rpx;
  font-weight: 700;
}
.preview-up {
  color: #f87171;
}
.preview-down {
  color: #34d399;
}
.preview-neutral {
  color: var(--color-text-muted, #94a3b8);
}
.preview-hint {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  margin-top: 4rpx;
}

.bottom-spacer {
  height: 180rpx;
}

.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.95);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  z-index: 50;
}
.cta-btn {
  flex: 1;
  padding: 22rpx 0;
  text-align: center;
  border-radius: 999rpx;
}
.cta-btn-primary {
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.cta-btn-primary-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.cta-btn-disabled {
  opacity: 0.5;
}
.cta-btn-danger {
  flex: 0 0 200rpx;
  background: rgba(239, 68, 68, 0.12);
  border: 1rpx solid rgba(239, 68, 68, 0.4);
}
.cta-btn-danger-hover {
  background: rgba(239, 68, 68, 0.24);
}
.cta-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}
.cta-btn-text-danger {
  font-size: 28rpx;
  color: #ef4444;
  font-weight: 700;
}
</style>
