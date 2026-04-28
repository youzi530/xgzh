<script setup lang="ts">
/**
 * VIP 升级页 (FE-S3-004).
 *
 * 路由: ``/pages/vip/index``
 *
 * 模块:
 * 1. **顶部 hero**: 金色渐变背景 + "升级 VIP" 标题 + 副标题
 * 2. **当前会员状态卡**: 拉 ``GET /vip/me`` 显示 trialing / active / expired / 无订阅
 *    - active: "VIP 至 2026-XX-XX 到期, 续费 X 个月" CTA
 *    - trialing: "VIP 试用中, 剩余 X 天, 立即升级" CTA
 *    - expired / null: 默认升级 CTA
 * 3. **4 张套餐卡** (月 ¥39 / 季 ¥99 / 年 ¥299 / 终身 ¥999):
 *    - 默认选中年度 (spec/06 §2.2 首推年度); 卡上 "推荐" 角标
 *    - 终身额外 "限时" 角标; 季度卡突出 "比月度便宜 16%"
 * 4. **权益矩阵**: 13 行权益, 免费 vs VIP 双列勾叉 (spec/06 §2.1 完整列表)
 * 5. **底部 sticky CTA**: "立即开通 ¥XXX" — 数字根据选中套餐动态变, 点 → ``payWithPlan``
 * 6. **法律小字**: 用户协议 / 已支付不退款 / 仅 MP-WEIXIN 支付提示
 *
 * 设计取舍:
 *
 * - **价格硬编码 vs 远程下发**: 当前阶段套餐价不会高频改, 走硬编码 + 服务端
 *   ``PLAN_PRICES_CNY`` 单一权威表对账. 未来做营销活动 (春节优惠 / 新人大礼包) 时
 *   再加 ``GET /api/v1/vip/pricing`` 远程下发. 客户端价目仅用于"快速渲染 + 视觉
 *   对比", 实际下单金额取 ``CreateOrderResponse.amount_cny`` 服务端权威值
 *
 * - **跨端守卫不阻塞 UI 渲染**: H5 / App 端也能完整看到套餐 / 权益 / 价格;
 *   只在底部 CTA 处把"立即开通"改成"请在小程序内支付", 让用户清楚"卖什么 + 多少
 *   钱", 比直接拒绝渲染好 (合规 + 转化率)
 *
 * - **不在本页轮询 membership**: 用户进页面时拉一次 ``refreshMembership``,
 *   不开 setInterval. 支付完成后跳到 result 页, 那边集中做轮询逻辑 (1.5s × 3 次)
 *
 * - **不复用 ``UpgradeVipModal`` 内部权益清单**: modal 是"激发购买决策"小卡片
 *   (5 项 highlight); 升级页是"完整对比"长清单 (13 项含勾叉对照表), 形态不同
 *
 * - **bottom sticky CTA 而非顶部固定**: spec/03 § 移动端首屏首焦原则 — 用户从上
 *   往下浏览权益清单, CTA 出现在浏览结束位置才有最高点击率, 顶部 CTA 用户还没
 *   看完权益就跳支付容易"被骗"感
 */

import { onLoad, onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import type { PayablePlan } from '@/api/payment'
import { useUpgradeModal } from '@/composables/upgradeModal'
import { useAuthStore } from '@/stores/auth'

const authStore = useAuthStore()
const { user, vipMembership, vipMembershipLoading } = storeToRefs(authStore)
const upgrade = useUpgradeModal()

const isMpWeixin = process.env.UNI_PLATFORM === 'mp-weixin'

interface PlanCard {
  plan: PayablePlan
  /** 标题 (套餐名) */
  title: string
  /** 主价 (展示用; 实际下单金额仍走 BE 权威 amount_cny) */
  price: number
  /** 副价文案 (例 "¥39/月", "限时立减 ¥200") */
  subtitle: string
  /** 折算每月单价, 帮用户做"年付划算"决策 */
  monthly: string
  /** 角标文案; null 不显 */
  badge: string | null
  /** 是否高亮 (默认选中态视觉) */
  recommended: boolean
}

/**
 * 4 套餐配置; 价格与 BE ``PLAN_PRICES_CNY`` 一致, 改价时两边同步.
 *
 * 排序: 月 → 季 → 年 → 终身, 视觉上从左到右"价格递增, 性价比递增", 用户脑路里
 * "右边贵但更划算" 是熟悉的 SaaS 套餐 pattern.
 */
const planCards: PlanCard[] = [
  {
    plan: 'monthly',
    title: '月度',
    price: 39,
    subtitle: '¥39 / 月',
    monthly: '¥39 / 月',
    badge: null,
    recommended: false,
  },
  {
    plan: 'quarterly',
    title: '季度',
    price: 99,
    subtitle: '¥99 / 3 个月',
    monthly: '¥33 / 月',
    badge: '比月度省 15%',
    recommended: false,
  },
  {
    plan: 'yearly',
    title: '年度',
    price: 299,
    subtitle: '¥299 / 12 个月',
    monthly: '¥24.9 / 月',
    badge: '推荐',
    recommended: true,
  },
  {
    plan: 'lifetime',
    title: '终身',
    price: 999,
    subtitle: '一次买断, 永不续费',
    monthly: '一次性',
    badge: '限时',
    recommended: false,
  },
]

/**
 * 权益矩阵 (spec/06 §2.1); 13 行勾叉对照.
 *
 * ``free`` / ``vip`` 字段: ``true`` 显 ✓ (绿), ``false`` 显 ✗ (灰), 字符串显文字
 * (例 "5 次/天" / "不限次"); UI 模板里统一用 ``renderCell`` 渲染 3 形态
 */
interface PerkRow {
  label: string
  free: boolean | string
  vip: boolean | string
}

const perkMatrix: PerkRow[] = [
  { label: 'AI 智能诊断', free: '5 次 / 天', vip: '不限次' },
  { label: 'IPO 详情 + 招股书', free: true, vip: true },
  { label: '分红 / 中签率历史数据 (5 年)', free: false, vip: true },
  { label: '招股书 AI 摘要 + 关键句高亮', free: false, vip: true },
  { label: '券商对比 + 实时费率', free: '基础信息', vip: '完整费率 + 计算器' },
  { label: 'CRS 跨境税务向导', free: false, vip: true },
  { label: '自选股 + 申购窗口提醒', free: '关注 ≤ 5 只', vip: '不限关注数' },
  { label: '暗盘 / 上市 3 档窗口推送', free: false, vip: true },
  { label: '历史打新数据 CSV 下载', free: false, vip: true },
  { label: '机构看法 + 文章 TL;DR', free: '只读', vip: '完整论据 + 来源' },
  { label: '客服优先响应', free: false, vip: true },
  { label: '广告 / 推荐位', free: '有', vip: '无' },
  { label: '小程序专享深色 UI', free: true, vip: true },
]

const selectedPlan = ref<PayablePlan>('yearly')
const submitting = ref(false)

const selectedCard = computed(() => planCards.find((p) => p.plan === selectedPlan.value)!)

/**
 * 当前订阅状态对应文案 / CTA 行为.
 *
 * 4 态:
 * - ``active``    显示"VIP 至 YYYY-MM-DD 到期", CTA 文案"续费 ¥XXX"
 * - ``trialing``  显示"VIP 试用中, 剩余 X 天", CTA 文案"立即升级 ¥XXX"
 * - ``expired``   显示"VIP 已过期", CTA 文案"重新订阅 ¥XXX"
 * - ``null``      显示"免费会员", CTA 文案"立即开通 ¥XXX"
 */
const membershipStatusText = computed(() => {
  const m = vipMembership.value
  if (!m || !m.has_active) {
    if (m?.status === 'expired') return 'VIP 已过期'
    if (m?.status === 'cancelled') return 'VIP 已取消'
    return '免费会员'
  }
  if (m.status === 'trialing') {
    return `VIP 试用中 · 剩余 ${m.days_remaining ?? '?'} 天`
  }
  if (m.plan === 'lifetime') {
    return '终身 VIP'
  }
  return `VIP 至 ${formatDate(m.end_at)} 到期`
})

const ctaText = computed(() => {
  if (!isMpWeixin) return '请在微信小程序内支付'
  if (submitting.value) return '正在唤起微信支付...'
  const m = vipMembership.value
  const price = `¥${selectedCard.value.price}`
  if (m?.status === 'active') return `续费 ${price}`
  if (m?.status === 'trialing') return `立即升级 ${price}`
  if (m?.status === 'expired' || m?.status === 'cancelled') return `重新订阅 ${price}`
  return `立即开通 ${price}`
})

function formatDate(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function selectPlan(plan: PayablePlan) {
  if (submitting.value) return
  selectedPlan.value = plan
}

/**
 * 主 CTA: 下单 + 拉起微信支付.
 *
 * 跨端: 仅 MP-WEIXIN 走真实 ``payWithPlan``; H5 / App 显占位 modal 提示去小程序付.
 *
 * 流程:
 * 1. 登录守卫: 未登录跳登录页, 登录回来用户重新点 (不自动 retry, 防 silent retry
 *    在错误身份下扣到错账)
 * 2. ``submitting`` 期间禁用按钮, 防双击 (BE 也有 5min idempotency 兜底, 但前端
 *    UX 上"按钮 loading + 禁用"比"成功后第二次提示已存在订单"清爽)
 * 3. 结果分流:
 *    - ``ok``         → 跳 ``/pages/vip/result?status=success&order_id=XXX``
 *    - ``cancel``     → 不跳页, 仅显示 "已取消" toast
 *    - ``failed``     → 不跳页, toast 错误信息 + 把 submitting 置 false
 *    - ``unsupported``→ 跳 ``/pages/vip/result?status=unsupported`` 让用户去小程序
 */
async function handleConfirm() {
  if (submitting.value) return

  if (!isMpWeixin) {
    uni.showModal({
      title: '请在微信小程序内支付',
      content:
        '微信支付仅在 "新股智汇" 微信小程序内可用. 请扫码进入小程序后选择套餐完成支付.',
      showCancel: false,
      confirmText: '我知道了',
    })
    return
  }

  if (!authStore.loggedIn) {
    uni.showToast({ title: '请先登录', icon: 'none' })
    setTimeout(() => {
      uni.navigateTo({ url: '/pages/auth/login' })
    }, 600)
    return
  }

  submitting.value = true
  try {
    const result = await upgrade.payWithPlan(selectedPlan.value)
    if (result.kind === 'ok') {
      uni.redirectTo({
        url: `/pages/vip/result?status=success&order_id=${result.order.order_id}&plan=${result.order.plan}`,
      })
      return
    }
    if (result.kind === 'cancel') {
      uni.showToast({ title: '已取消支付', icon: 'none' })
      return
    }
    if (result.kind === 'unsupported') {
      uni.showModal({
        title: '请在小程序内支付',
        content: result.message,
        showCancel: false,
      })
      return
    }
    // failed
    uni.showToast({
      title: result.message || '支付失败, 请重试',
      icon: 'none',
      duration: 2400,
    })
  } finally {
    submitting.value = false
  }
}

/**
 * 取消订阅 / 管理订阅入口 (spec/06 §会员续订要求合规位).
 *
 * 微信支付 v3 没有"小程序内取消订阅"页可跳; 实际生态是用户在 "微信 → 我 →
 * 服务 → 钱包 → 支付管理 → 自动续费" 列表里取消. 这里给个固定文案指引,
 * 不放假按钮装作能跳.
 */
function showManageSubscription() {
  uni.showModal({
    title: '管理订阅',
    content:
      '本应用当前为单次支付订阅 (非自动续费), 到期后会自动失效, 不会自动扣款. 已支付订阅不退款.',
    showCancel: false,
    confirmText: '我知道了',
  })
}

/**
 * 协议入口; 与 me 页 ``openLegal`` 同款占位, 上线前替换为 webview.
 */
function openLegal(kind: 'tos' | 'disclaimer') {
  const map = {
    tos: {
      title: 'VIP 服务条款',
      content:
        '订阅 VIP 视为同意以下条款:\n1) 平台仅作为信息聚合工具, 所有 AI 输出不构成投资建议\n2) 已支付订阅在适用法律允许范围内不退款\n3) 单次支付订阅到期自动失效, 不自动续费\n4) 订阅期间享受套餐对应权益, 平台保留权益调整权利',
    },
    disclaimer: {
      title: '免责声明',
      content:
        'AI 分析结果仅供参考, 不构成投资 / 税务 / 法律建议. 数据来源已在各页底部标注. 投资有风险, 入市需谨慎.',
    },
  }
  const item = map[kind]
  uni.showModal({
    title: item.title,
    content: item.content,
    showCancel: false,
    confirmText: '我知道了',
  })
}

onLoad(() => {
  // 进页面时拉一次最新会员状态; 失败不阻塞渲染 (套餐 + 权益矩阵不依赖)
  if (authStore.loggedIn) {
    void authStore.refreshMembership()
  }
})

onShow(() => {
  // 从 result 页 navigateBack 回来时刷新一次 (用户可能刚续费成功)
  if (authStore.loggedIn) {
    void authStore.refreshMembership()
  }
})
</script>

<template>
  <view class="page">
    <!-- ─── 顶部 hero ─── -->
    <view class="hero">
      <text class="hero-crown">👑</text>
      <text class="hero-title">XGZH VIP</text>
      <text class="hero-subtitle">解锁全部 AI 深度功能, 让每次决策更靠谱</text>
    </view>

    <!-- ─── 当前订阅状态卡 ─── -->
    <view v-if="user" class="status-card" :class="{ 'status-active': vipMembership?.has_active }">
      <view class="status-row">
        <text class="status-label">当前会员状态</text>
        <text class="status-value">{{ vipMembershipLoading ? '加载中…' : membershipStatusText }}</text>
      </view>
      <view v-if="vipMembership?.has_active && vipMembership.plan !== 'lifetime'" class="status-foot">
        <text class="status-foot-text">续费在原到期日基础上累加</text>
      </view>
    </view>

    <!-- ─── 套餐卡 ─── -->
    <view class="section-title">选择套餐</view>
    <scroll-view scroll-x class="plans" :show-scrollbar="false">
      <view
        v-for="card in planCards"
        :key="card.plan"
        :class="['plan-card', selectedPlan === card.plan && 'plan-card-selected', card.recommended && 'plan-card-recommended']"
        @tap="selectPlan(card.plan)"
      >
        <text v-if="card.badge" class="plan-badge">{{ card.badge }}</text>
        <text class="plan-title">{{ card.title }}</text>
        <view class="plan-price-row">
          <text class="plan-currency">¥</text>
          <text class="plan-price">{{ card.price }}</text>
        </view>
        <text class="plan-subtitle">{{ card.subtitle }}</text>
        <text class="plan-monthly">{{ card.monthly }}</text>
        <view v-if="selectedPlan === card.plan" class="plan-check">
          <text class="plan-check-icon">✓</text>
        </view>
      </view>
    </scroll-view>

    <!-- ─── 权益矩阵 ─── -->
    <view class="section-title">权益对比</view>
    <view class="matrix">
      <view class="matrix-header">
        <text class="matrix-col-label">权益</text>
        <text class="matrix-col-free">免费</text>
        <text class="matrix-col-vip">VIP</text>
      </view>
      <view v-for="row in perkMatrix" :key="row.label" class="matrix-row">
        <text class="matrix-col-label">{{ row.label }}</text>

        <view class="matrix-cell">
          <text v-if="row.free === true" class="matrix-yes">✓</text>
          <text v-else-if="row.free === false" class="matrix-no">✗</text>
          <text v-else class="matrix-text matrix-text-muted">{{ row.free }}</text>
        </view>

        <view class="matrix-cell">
          <text v-if="row.vip === true" class="matrix-yes matrix-yes-vip">✓</text>
          <text v-else-if="row.vip === false" class="matrix-no">✗</text>
          <text v-else class="matrix-text matrix-text-vip">{{ row.vip }}</text>
        </view>
      </view>
    </view>

    <!-- ─── 法律小字 ─── -->
    <view class="legal">
      <text class="legal-line" @tap="openLegal('tos')">VIP 服务条款 ›</text>
      <text class="legal-line" @tap="openLegal('disclaimer')">免责声明 ›</text>
      <text class="legal-line" @tap="showManageSubscription">管理订阅 ›</text>
      <text class="legal-fineprint">
        订阅即同意 VIP 服务条款; 平台仅作为信息聚合工具, 所有 AI 输出不构成投资建议. 已支付订阅在适用法律允许范围内不退款.
      </text>
    </view>

    <view class="bottom-spacer" />

    <!-- ─── 底部 sticky CTA ─── -->
    <view class="cta-bar">
      <view class="cta-meta">
        <text class="cta-meta-title">{{ selectedCard.title }}</text>
        <text class="cta-meta-sub">{{ selectedCard.subtitle }}</text>
      </view>
      <view
        :class="['cta-btn', submitting && 'cta-btn-loading', !isMpWeixin && 'cta-btn-disabled']"
        hover-class="cta-btn-hover"
        :hover-stay-time="80"
        @tap="handleConfirm"
      >
        <text class="cta-btn-text">{{ ctaText }}</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 24rpx 0;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

/* ─── hero ─── */
.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  padding: 32rpx 0 16rpx;
  background: radial-gradient(circle at top, rgba(246, 196, 83, 0.18), transparent 70%);
}

.hero-crown {
  font-size: 80rpx;
  line-height: 1;
}

.hero-title {
  font-size: 48rpx;
  font-weight: 800;
  letter-spacing: 4rpx;
  color: #f6c453;
}

.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

/* ─── status card ─── */
.status-card {
  padding: 24rpx 28rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}

.status-active {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.15), rgba(79, 139, 255, 0.08));
  border-color: rgba(246, 196, 83, 0.32);
}

.status-row {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}

.status-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.status-value {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.status-active .status-value {
  color: #f6c453;
}

.status-foot {
  margin-top: 4rpx;
}

.status-foot-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.8;
}

/* ─── section titles ─── */
.section-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  margin-top: 8rpx;
}

/* ─── plan cards ─── */
.plans {
  white-space: nowrap;
  /* margin-x 抵消 page padding-x, 让滚动卡片能贴边滚 */
  margin: 0 -24rpx;
  padding: 8rpx 24rpx;
}

.plan-card {
  display: inline-flex;
  flex-direction: column;
  vertical-align: top;
  width: 240rpx;
  margin-right: 16rpx;
  padding: 28rpx 20rpx 20rpx;
  background: var(--color-surface, #131a2c);
  border: 2rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 24rpx;
  position: relative;
  overflow: visible;
}

.plan-card-selected {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.16), rgba(217, 119, 6, 0.06));
  border-color: #f6c453;
  box-shadow: 0 4rpx 20rpx rgba(246, 196, 83, 0.18);
}

.plan-card-recommended:not(.plan-card-selected) {
  border-color: rgba(246, 196, 83, 0.32);
}

.plan-badge {
  position: absolute;
  top: -16rpx;
  right: 16rpx;
  padding: 4rpx 16rpx;
  background: linear-gradient(135deg, #f6c453, #d97706);
  color: #1a1305;
  font-size: 20rpx;
  font-weight: 700;
  border-radius: 999rpx;
}

.plan-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.plan-price-row {
  display: flex;
  flex-direction: row;
  align-items: baseline;
  gap: 4rpx;
  margin-top: 12rpx;
}

.plan-currency {
  font-size: 24rpx;
  font-weight: 600;
  color: #f6c453;
}

.plan-price {
  font-size: 56rpx;
  font-weight: 800;
  line-height: 1;
  color: #f6c453;
}

.plan-subtitle {
  margin-top: 8rpx;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.plan-monthly {
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text, #e2e8f0);
  opacity: 0.7;
}

.plan-check {
  position: absolute;
  top: 12rpx;
  left: 12rpx;
  width: 36rpx;
  height: 36rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #f6c453, #d97706);
  display: flex;
  align-items: center;
  justify-content: center;
}

.plan-check-icon {
  font-size: 24rpx;
  font-weight: 700;
  color: #1a1305;
}

/* ─── matrix ─── */
.matrix {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  overflow: hidden;
}

.matrix-header,
.matrix-row {
  display: flex;
  flex-direction: row;
  align-items: center;
  padding: 20rpx 24rpx;
  border-bottom: 1rpx solid rgba(255, 255, 255, 0.04);
}

.matrix-row:last-child {
  border-bottom: none;
}

.matrix-header {
  background: rgba(255, 255, 255, 0.04);
}

.matrix-col-label {
  flex: 1;
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}

.matrix-header .matrix-col-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-weight: 600;
}

.matrix-col-free,
.matrix-col-vip {
  width: 120rpx;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-weight: 600;
}

.matrix-col-vip {
  color: #f6c453;
}

.matrix-cell {
  width: 120rpx;
  display: flex;
  align-items: center;
  justify-content: center;
}

.matrix-yes {
  font-size: 28rpx;
  font-weight: 700;
  color: #22c55e;
}

.matrix-yes-vip {
  color: #f6c453;
}

.matrix-no {
  font-size: 26rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.5;
}

.matrix-text {
  font-size: 22rpx;
  text-align: center;
  line-height: 1.4;
}

.matrix-text-muted {
  color: var(--color-text-muted, #94a3b8);
}

.matrix-text-vip {
  color: #f6c453;
  font-weight: 600;
}

/* ─── legal ─── */
.legal {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  padding: 0 4rpx;
}

.legal-line {
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
  padding: 8rpx 0;
}

.legal-fineprint {
  margin-top: 8rpx;
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
  line-height: 1.6;
}

.bottom-spacer {
  height: 200rpx;
}

/* ─── sticky CTA ─── */
.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
  padding: 16rpx 24rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.92);
  border-top: 1rpx solid rgba(246, 196, 83, 0.18);
  /* MP-WEIXIN 上 backdrop-filter 部分版本不支持, 退化为半透明纯色背景仍可读 */
  backdrop-filter: blur(8rpx);
}

.cta-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 2rpx;
  min-width: 0;
}

.cta-meta-title {
  font-size: 26rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}

.cta-meta-sub {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
}

.cta-btn {
  flex-shrink: 0;
  padding: 22rpx 48rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #f6c453, #d97706);
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.32);
}

.cta-btn-hover {
  background: linear-gradient(135deg, #d97706, #b45309);
}

.cta-btn-disabled {
  background: rgba(148, 163, 184, 0.4);
  box-shadow: none;
}

.cta-btn-loading {
  opacity: 0.85;
}

.cta-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #1a1305;
}

.cta-btn-disabled .cta-btn-text {
  color: rgba(11, 18, 32, 0.7);
}
</style>
