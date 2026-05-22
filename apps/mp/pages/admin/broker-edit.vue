<script setup lang="ts">
/**
 * Admin 券商新建 / 编辑页 (Sprint 11 FE-S11-A01).
 *
 * 路由:
 * - ``/pages/admin/broker-edit?new=1`` 新建
 * - ``/pages/admin/broker-edit?slug=xxx`` 编辑
 *
 * 字段:
 * - 基本: slug (新建可改 / 编辑只读) / name_zh / name_en / logo_url
 * - 开户: open_account_url (顶层, 优先级 > promotion.referral_url; admin 编辑入口)
 * - 市场: 4 checkbox HK / A / US / SG
 * - 上下架: switch is_active
 * - 排序: number input display_order (越大越靠前)
 * - 促销文案: title / description / is_active / referral_url 4 字段 (折叠面板,
 *   admin 直接读写 JSONB promotion key)
 * - partnership: partnership_type select + cpa_amount / cps_rate (折叠面板)
 *
 * 保存策略:
 * - 新建走 POST /admin/brokers (整体 body); 编辑走 PATCH (只传改了的字段)
 * - 删除走 DELETE (软删) + 二次 modal 确认 (高破坏性)
 */

import { onLoad } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { reactive, ref } from 'vue'

import {
  createAdminBroker,
  deleteAdminBroker,
  getAdminBrokerDetail,
  parseAdminBrokerError,
  restoreAdminBroker,
  updateAdminBroker,
  type BrokerCreate,
  type BrokerUpdate,
  type MarketSupport,
  type PartnershipType,
} from '@/api/admin-brokers'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const isNew = ref<boolean>(false)
const phase = ref<'loading' | 'ready' | 'saving' | 'deleting'>('loading')
const originalSlug = ref<string>('') // 编辑模式下原 slug, 用于 PATCH path
const isDeleted = ref<boolean>(false)

interface FormState {
  slug: string
  name_zh: string
  name_en: string
  logo_url: string
  open_account_url: string
  market_HK: boolean
  market_A: boolean
  market_US: boolean
  market_SG: boolean
  is_active: boolean
  display_order: number
  partnership_type: PartnershipType
  partnership_cpa_amount: string
  partnership_cps_rate: string
  promo_is_active: boolean
  promo_title: string
  promo_description: string
  promo_referral_url: string
}

const form = reactive<FormState>({
  slug: '',
  name_zh: '',
  name_en: '',
  logo_url: '',
  open_account_url: '',
  market_HK: false,
  market_A: false,
  market_US: false,
  market_SG: false,
  is_active: true,
  display_order: 0,
  partnership_type: 'NONE',
  partnership_cpa_amount: '',
  partnership_cps_rate: '',
  promo_is_active: false,
  promo_title: '',
  promo_description: '',
  promo_referral_url: '',
})

const showPromoPanel = ref<boolean>(false)
const showPartnershipPanel = ref<boolean>(false)

function marketArray(): MarketSupport[] {
  const arr: MarketSupport[] = []
  if (form.market_HK) arr.push('HK')
  if (form.market_A) arr.push('A')
  if (form.market_US) arr.push('US')
  if (form.market_SG) arr.push('SG')
  return arr
}

async function loadDetail(slug: string) {
  phase.value = 'loading'
  try {
    const detail = await getAdminBrokerDetail(slug)
    originalSlug.value = detail.slug
    isDeleted.value = detail.is_deleted
    form.slug = detail.slug
    form.name_zh = detail.name_zh
    form.name_en = detail.name_en ?? ''
    form.logo_url = detail.logo_url ?? ''
    form.open_account_url = detail.open_account_url ?? ''
    form.market_HK = detail.market_support.includes('HK')
    form.market_A = detail.market_support.includes('A')
    form.market_US = detail.market_support.includes('US')
    form.market_SG = detail.market_support.includes('SG')
    form.is_active = detail.is_active
    form.display_order = detail.display_order
    form.partnership_type = detail.partnership_type
    form.partnership_cpa_amount = detail.partnership_cpa_amount ?? ''
    form.partnership_cps_rate = detail.partnership_cps_rate ?? ''
    const promo = detail.promotion as Record<string, unknown>
    form.promo_is_active = Boolean(promo?.is_active)
    form.promo_title = (promo?.title as string) ?? ''
    form.promo_description = (promo?.description as string) ?? ''
    form.promo_referral_url = (promo?.referral_url as string) ?? ''
    phase.value = 'ready'
  } catch (err) {
    const { code, message } = parseAdminBrokerError(err)
    uni.showToast({ title: code === 'broker_not_found' ? '券商不存在' : message, icon: 'none' })
    setTimeout(() => uni.navigateBack(), 800)
  }
}

function buildCreatePayload(): BrokerCreate {
  return {
    slug: form.slug.trim(),
    name_zh: form.name_zh.trim(),
    name_en: form.name_en.trim() || null,
    logo_url: form.logo_url.trim() || null,
    open_account_url: form.open_account_url.trim() || null,
    market_support: marketArray(),
    promotion: {
      is_active: form.promo_is_active,
      title: form.promo_title.trim(),
      description: form.promo_description.trim(),
      referral_url: form.promo_referral_url.trim(),
    },
    partnership_type: form.partnership_type,
    partnership_cpa_amount: form.partnership_cpa_amount.trim() || null,
    partnership_cps_rate: form.partnership_cps_rate.trim() || null,
    display_order: form.display_order,
    is_active: form.is_active,
  }
}

function buildUpdatePayload(): BrokerUpdate {
  return {
    name_zh: form.name_zh.trim(),
    name_en: form.name_en.trim(),
    logo_url: form.logo_url.trim(),
    open_account_url: form.open_account_url.trim(),
    market_support: marketArray(),
    display_order: form.display_order,
    is_active: form.is_active,
    partnership_type: form.partnership_type,
    partnership_cpa_amount: form.partnership_cpa_amount.trim() || null,
    partnership_cps_rate: form.partnership_cps_rate.trim() || null,
    promotion_patch: {
      is_active: form.promo_is_active,
      title: form.promo_title.trim(),
      description: form.promo_description.trim(),
      referral_url: form.promo_referral_url.trim(),
    },
  }
}

async function onSave() {
  if (!form.slug.trim() || !form.name_zh.trim()) {
    uni.showToast({ title: 'slug 和名称必填', icon: 'none' })
    return
  }
  phase.value = 'saving'
  try {
    if (isNew.value) {
      await createAdminBroker(buildCreatePayload())
      uni.showToast({ title: '已新建', icon: 'success' })
    } else {
      await updateAdminBroker(originalSlug.value, buildUpdatePayload())
      uni.showToast({ title: '已保存', icon: 'success' })
    }
    setTimeout(() => uni.navigateBack(), 600)
  } catch (err) {
    const { code, message } = parseAdminBrokerError(err)
    let toast = message
    if (code === 'broker_slug_taken') toast = 'slug 已被占用'
    else if (code === 'validation_error') toast = '请检查字段格式'
    uni.showToast({ title: toast || '保存失败', icon: 'none' })
    phase.value = 'ready'
  }
}

async function onDelete() {
  const confirm1 = await new Promise<boolean>((resolve) =>
    uni.showModal({
      title: '确认删除',
      content: `确认软删 "${form.name_zh}"? 此操作可恢复.`,
      confirmText: '继续',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    }),
  )
  if (!confirm1) return

  const confirm2 = await new Promise<boolean>((resolve) =>
    uni.showModal({
      title: '最终确认',
      content: '请再次确认删除. FE 列表不再展示此券商.',
      confirmText: '确认删除',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    }),
  )
  if (!confirm2) return

  phase.value = 'deleting'
  try {
    await deleteAdminBroker(originalSlug.value)
    uni.showToast({ title: '已删除', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 600)
  } catch (err) {
    const { message } = parseAdminBrokerError(err)
    uni.showToast({ title: message || '删除失败', icon: 'none' })
    phase.value = 'ready'
  }
}

async function onRestore() {
  phase.value = 'saving'
  try {
    await restoreAdminBroker(originalSlug.value)
    uni.showToast({ title: '已恢复', icon: 'success' })
    isDeleted.value = false
  } catch (err) {
    const { message } = parseAdminBrokerError(err)
    uni.showToast({ title: message || '恢复失败', icon: 'none' })
  } finally {
    phase.value = 'ready'
  }
}

onLoad(async (query: Record<string, string | undefined> | undefined) => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  if (query?.new === '1') {
    isNew.value = true
    phase.value = 'ready'
    uni.setNavigationBarTitle({ title: '新建券商' })
    return
  }
  if (query?.slug) {
    isNew.value = false
    uni.setNavigationBarTitle({ title: '编辑券商' })
    await loadDetail(query.slug)
    return
  }
  uni.showToast({ title: '参数缺失', icon: 'none' })
  setTimeout(() => uni.navigateBack(), 500)
})
</script>

<template>
  <view class="page">
    <view v-if="phase === 'loading'" class="state">
      <text>加载中...</text>
    </view>

    <view v-else class="form">
      <view v-if="isDeleted" class="banner-deleted">
        <text>此券商已软删. 恢复后才能继续编辑.</text>
        <view class="restore-btn" @tap="onRestore">
          <text>恢复</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>基本信息</text>
        </view>

        <view class="field">
          <text class="label">slug (URL 标识)</text>
          <input
            v-model="form.slug"
            class="input"
            :disabled="!isNew"
            placeholder="如 futubull (小写字母数字 + 连字符)"
          />
        </view>

        <view class="field">
          <text class="label">中文名 *</text>
          <input v-model="form.name_zh" class="input" placeholder="如 富途证券" />
        </view>

        <view class="field">
          <text class="label">英文名</text>
          <input v-model="form.name_en" class="input" placeholder="如 Futu" />
        </view>

        <view class="field">
          <text class="label">Logo URL</text>
          <input v-model="form.logo_url" class="input" placeholder="https://..." />
        </view>

        <view class="field">
          <text class="label">开户链接 (admin 维护)</text>
          <input
            v-model="form.open_account_url"
            class="input"
            placeholder="https://broker.example.com/open?ref=..."
          />
          <text class="help">优先于 promotion.referral_url; admin 改这里即可生效</text>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>市场支持</text>
        </view>
        <view class="market-row">
          <label class="market-item">
            <checkbox :checked="form.market_HK" @tap="form.market_HK = !form.market_HK" />
            <text>HK 港股</text>
          </label>
          <label class="market-item">
            <checkbox :checked="form.market_A" @tap="form.market_A = !form.market_A" />
            <text>A 股</text>
          </label>
          <label class="market-item">
            <checkbox :checked="form.market_US" @tap="form.market_US = !form.market_US" />
            <text>US 美股</text>
          </label>
          <label class="market-item">
            <checkbox :checked="form.market_SG" @tap="form.market_SG = !form.market_SG" />
            <text>SG 新加坡</text>
          </label>
        </view>
      </view>

      <view class="section">
        <view class="section-title">
          <text>展示控制</text>
        </view>

        <view class="field-row">
          <text class="label">上架</text>
          <switch :checked="form.is_active" @change="(e: any) => (form.is_active = e.detail.value)" />
        </view>

        <view class="field">
          <text class="label">排序权重 (越大越靠前, 0-9999)</text>
          <input
            v-model.number="form.display_order"
            class="input"
            type="number"
            placeholder="0"
          />
        </view>
      </view>

      <view class="section">
        <view class="section-title section-title-collapsible" @tap="showPromoPanel = !showPromoPanel">
          <text>促销文案 {{ showPromoPanel ? '▾' : '▸' }}</text>
        </view>
        <view v-if="showPromoPanel" class="panel-body">
          <view class="field-row">
            <text class="label">促销启用</text>
            <switch
              :checked="form.promo_is_active"
              @change="(e: any) => (form.promo_is_active = e.detail.value)"
            />
          </view>
          <view class="field">
            <text class="label">标题</text>
            <input v-model="form.promo_title" class="input" placeholder="如 新用户开户送 100 美元" />
          </view>
          <view class="field">
            <text class="label">描述</text>
            <textarea v-model="form.promo_description" class="textarea" placeholder="详细描述" />
          </view>
          <view class="field">
            <text class="label">备用开户链接 (promotion.referral_url)</text>
            <input v-model="form.promo_referral_url" class="input" />
            <text class="help">仅当顶层"开户链接"留空时, 才会用这里作为 fallback</text>
          </view>
        </view>
      </view>

      <view class="section">
        <view
          class="section-title section-title-collapsible"
          @tap="showPartnershipPanel = !showPartnershipPanel"
        >
          <text>合作类型 (内部) {{ showPartnershipPanel ? '▾' : '▸' }}</text>
        </view>
        <view v-if="showPartnershipPanel" class="panel-body">
          <view class="field">
            <text class="label">类型</text>
            <picker
              :value="['NONE', 'CPA', 'CPS', 'BOTH'].indexOf(form.partnership_type)"
              :range="['NONE', 'CPA', 'CPS', 'BOTH']"
              @change="(e: any) => (form.partnership_type = (['NONE', 'CPA', 'CPS', 'BOTH'] as PartnershipType[])[e.detail.value])"
            >
              <view class="picker-display">
                <text>{{ form.partnership_type }}</text>
              </view>
            </picker>
          </view>
          <view class="field">
            <text class="label">CPA 单次返佣 CNY</text>
            <input v-model="form.partnership_cpa_amount" class="input" placeholder="如 150.00" />
          </view>
          <view class="field">
            <text class="label">CPS 分成比例 (0-1)</text>
            <input v-model="form.partnership_cps_rate" class="input" placeholder="如 0.025" />
          </view>
        </view>
      </view>

      <view class="actions">
        <view class="save-btn" :class="{ 'is-saving': phase === 'saving' }" @tap="onSave">
          <text>{{ phase === 'saving' ? '保存中...' : isNew ? '新建' : '保存' }}</text>
        </view>
        <view v-if="!isNew && !isDeleted" class="delete-btn" @tap="onDelete">
          <text>{{ phase === 'deleting' ? '删除中...' : '软删' }}</text>
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

.section-title-collapsible {
  cursor: pointer;
}

.panel-body {
  border-top: 1rpx solid #1f2942;
  padding-top: 16rpx;
}

.field {
  margin-bottom: 20rpx;
}

.field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 20rpx;
}

.label {
  display: block;
  color: #8b9bb8;
  font-size: 24rpx;
  margin-bottom: 8rpx;
}

.help {
  display: block;
  color: #6b7794;
  font-size: 20rpx;
  margin-top: 4rpx;
}

.input,
.textarea {
  width: 100%;
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;
  color: #e4e7ee;
  font-size: 26rpx;
  box-sizing: border-box;
}

.textarea {
  min-height: 140rpx;
}

.market-row {
  display: flex;
  flex-wrap: wrap;
  gap: 16rpx;
}

.market-item {
  display: flex;
  align-items: center;
  gap: 8rpx;

  text {
    color: #e4e7ee;
    font-size: 26rpx;
  }
}

.picker-display {
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;

  text {
    color: #e4e7ee;
    font-size: 26rpx;
  }
}

.actions {
  display: flex;
  gap: 16rpx;
  margin-top: 24rpx;
}

.save-btn {
  flex: 1;
  padding: 24rpx;
  background-color: #3b82f6;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #ffffff;
    font-size: 30rpx;
    font-weight: 600;
  }

  &.is-saving {
    opacity: 0.6;
  }
}

.delete-btn {
  padding: 24rpx 40rpx;
  background-color: rgba(239, 68, 68, 0.18);
  border: 1rpx solid #ef4444;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #fca5a5;
    font-size: 30rpx;
  }
}
</style>
