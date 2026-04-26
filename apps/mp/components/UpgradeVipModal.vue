<script setup lang="ts">
/**
 * VIP 升级引导 modal (FE-S2-004).
 *
 * 多入口触发, 状态收敛到 ``composables/upgradeModal.ts`` 单例:
 *
 * - ``source = 'quota_banner'``  agent 页 429 banner 点 "升级 VIP"
 * - ``source = 'inline_error'``  assistant 气泡内嵌 quota 错误点 "升级"
 * - ``source = 'me_page'``       个人中心 VIP 卡片
 * - ``source = 'manual'``        其他默认入口
 *
 * 设计取舍
 * ========
 *
 * - **金色渐变主题**: 视觉上把 VIP 区与普通蓝主色拉开层级; 与 ``banner-quota`` 一致,
 *   用户从 banner 点进来不割裂
 *
 * - **权益列表硬编码 vs 远程下发**: 现阶段权益清单与 spec/06 一致, 不会高频改;
 *   远程下发 (``GET /api/v1/vip/perks``) 引入额外接口 + 灰度风险, 收益不大. Sprint 3
 *   做营销活动时再说
 *
 * - **配额尾巴仅 quota 来源时显**: ``source = 'me_page'`` 进来用户多半不在配额超额
 *   场景, 不显"今日 used / limit", 避免"我没用过怎么看到 5/5"这种困惑
 *
 * - **底部双 CTA 不是中央单按钮**: "立即升级"主操作 + "稍后再说"次要操作并排,
 *   降低用户被强迫升级的感受 (合规上 "暗黑模式" 红线之一)
 *
 * - **模板嵌套不超过 4 层**: MP-WEIXIN 嵌套层数有性能上限, 实测 5 层以上 setData
 *   时延爆炸; 这里 mask > panel > content > section / actions, 4 层封顶
 *
 * - **不 v-if visible 而是 v-show**: 关弹时退场动画 (``opacity 0 + translateY 100%``)
 *   要求 DOM 仍存在; v-if 直接卸载, 退场无动画. v-show 保留 DOM, 退场用 CSS transition
 *   能跑完. 代价: 首屏多渲染一次 panel (但 ``display: none`` 不参与布局, 实际成本极低)
 *
 * Props
 * =====
 * 无 — 完全从 ``useUpgradeModal()`` 单例读 state, 不接受外部 props 防止状态错位
 *
 * Emits
 * =====
 * 无 — 关弹 / 跳支付都走 composable 暴露的 ``close`` / ``gotoPay``, 父页不需要
 * 接事件, 只在末尾 ``<UpgradeVipModal />`` 挂一次即可
 */

import { computed } from 'vue'

import { useUpgradeModal } from '@/composables/upgradeModal'

const upgrade = useUpgradeModal()

/**
 * 配额尾巴的展示条件:
 * - quota 来源 (banner / inline_error) + 拿到 quota payload 才显
 * - me_page 进来不显 (上下文不在"超额"场景)
 */
const showQuotaFooter = computed(() => {
  if (!upgrade.quota.value) return false
  return upgrade.source.value === 'quota_banner' || upgrade.source.value === 'inline_error'
})

/**
 * 顶部副标题文案根据来源切换;
 * quota 场景强调"今日额度已用完", me 页面纯营销
 */
const subtitle = computed(() => {
  switch (upgrade.source.value) {
    case 'quota_banner':
    case 'inline_error':
      return '解锁不限次 AI 调用 + 全部进阶功能'
    case 'me_page':
    default:
      return '解锁全部 AI 深度功能, 让每次决策更靠谱'
  }
})

/** 用量百分比 (0-100); VIP / 无限套餐返 100, 进度条满格表示已"全开" */
const usagePercent = computed(() => {
  const q = upgrade.quota.value
  if (!q) return 0
  if (q.limit < 0) return 100
  if (q.limit === 0) return 0
  return Math.min(100, Math.round((q.used / q.limit) * 100))
})

const planLabel = computed(() => {
  const q = upgrade.quota.value
  if (!q) return ''
  return ({ free: '免费', vip: 'VIP', anonymous: '匿名' } as const)[q.plan] || q.plan
})

const limitLabel = computed(() => {
  const q = upgrade.quota.value
  if (!q) return ''
  return q.limit < 0 ? '∞' : String(q.limit)
})

/** VIP 权益清单; 与 spec/06 §会员体系一致, 改文案在这里改即可 */
interface Perk {
  emoji: string
  title: string
  desc: string
  highlight?: boolean
}

const perks: Perk[] = [
  {
    emoji: '🤖',
    title: 'AI Agent 不限次',
    desc: '免费版每日 5 次, VIP 取消调用次数限制',
    highlight: true,
  },
  {
    emoji: '📊',
    title: '历史打新数据库',
    desc: '近 5 年 IPO 走势 / 中签率 / 上市首日表现, 可下载 CSV',
  },
  {
    emoji: '🔔',
    title: '无限自选 + 提醒',
    desc: '招股 / 暗盘 / 上市三档窗口提醒, 不限关注数量',
  },
  {
    emoji: '🌐',
    title: 'CRS 跨境税务向导',
    desc: '港 / A / 美 / 新 多区税务 + 申报流程一站说明',
  },
  {
    emoji: '🥇',
    title: '券商手续费比价',
    desc: '完整费率表 + 一键计算实付成本',
  },
]

function onMaskTap() {
  upgrade.close()
}

/** 阻止 panel 内点击穿透到 mask (否则点 panel 内任意位置都关弹) */
function noop() {}

function onClose() {
  upgrade.close()
}

function onUpgrade() {
  upgrade.gotoPay()
}
</script>

<template>
  <view v-show="upgrade.visible.value" class="uv-mask" @tap="onMaskTap">
    <view class="uv-panel" @tap.stop="noop">
      <!-- 顶部装饰条 + 关闭按钮 -->
      <view class="uv-handle" />
      <view class="uv-close" @tap="onClose">
        <text class="uv-close-x">×</text>
      </view>

      <!-- 标题区 (金色渐变背景) -->
      <view class="uv-header">
        <text class="uv-crown">👑</text>
        <text class="uv-title">升级 VIP</text>
        <text class="uv-subtitle">{{ subtitle }}</text>
      </view>

      <!-- 配额尾巴: 仅 quota 来源时展示当前用量 + 进度条 -->
      <view v-if="showQuotaFooter" class="uv-quota">
        <view class="uv-quota-head">
          <text class="uv-quota-label">当前 {{ planLabel }} 套餐</text>
          <text class="uv-quota-value">
            <text class="uv-quota-used">{{ upgrade.quota.value?.used ?? 0 }}</text>
            <text class="uv-quota-sep"> / </text>
            <text>{{ limitLabel }}</text>
          </text>
        </view>
        <view class="uv-progress-track">
          <view class="uv-progress-fill" :style="`width: ${usagePercent}%;`" />
        </view>
        <text
          v-if="upgrade.quota.value?.retry_after_seconds"
          class="uv-quota-retry"
        >
          约 {{ upgrade.quota.value.retry_after_seconds }} 秒后可重试; 升级 VIP 后立即解除限制
        </text>
        <text v-else class="uv-quota-retry">免费配额按滑动窗口计算, 升级 VIP 后无限制</text>
      </view>

      <!-- 权益清单 -->
      <scroll-view scroll-y class="uv-body" :enable-back-to-top="true">
        <view class="uv-perks">
          <view
            v-for="p in perks"
            :key="p.title"
            :class="['uv-perk', p.highlight && 'uv-perk-hl']"
          >
            <text class="uv-perk-emoji">{{ p.emoji }}</text>
            <view class="uv-perk-text">
              <text class="uv-perk-title">{{ p.title }}</text>
              <text class="uv-perk-desc">{{ p.desc }}</text>
            </view>
            <text v-if="p.highlight" class="uv-perk-tag">本次解锁</text>
          </view>
        </view>

        <!-- 法律 / 合规小字; 与 spec/06 §法律隔离一致 -->
        <text class="uv-legal">
          升级即同意 VIP 服务条款; 平台仅作为信息聚合工具, 所有 AI 输出不构成投资建议.
          会员订阅可随时取消, 已支付不退款 (适用法律允许范围内)
        </text>
      </scroll-view>

      <!-- 底部双 CTA -->
      <view class="uv-actions">
        <view class="uv-btn uv-btn-secondary" @tap="onClose">
          <text class="uv-btn-text-ghost">稍后再说</text>
        </view>
        <view class="uv-btn uv-btn-primary" @tap="onUpgrade">
          <text class="uv-btn-text">立即升级</text>
        </view>
      </view>

      <view class="uv-safe" />
    </view>
  </view>
</template>

<style lang="scss" scoped>
/* ───────── 容器 + mask ───────── */
.uv-mask {
  position: fixed;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(0, 0, 0, 0.62);
  /* 与 CitationDrawer 同 z-index 层 (999); 不会同时共存, 稳 */
  z-index: 999;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}

.uv-panel {
  position: relative;
  width: 100%;
  max-height: 86vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface, #131a2b);
  border-top-left-radius: 28rpx;
  border-top-right-radius: 28rpx;
  border-top: 1rpx solid rgba(246, 196, 83, 0.32);
  /* 整体光晕: 顶部金色 → 透明, 强化 VIP 氛围 */
  box-shadow: 0 -4rpx 32rpx rgba(246, 196, 83, 0.12);
  animation: uv-slide-up 0.24s ease-out;
}

@keyframes uv-slide-up {
  from {
    transform: translateY(100%);
  }
  to {
    transform: translateY(0);
  }
}

.uv-handle {
  align-self: center;
  width: 80rpx;
  height: 8rpx;
  margin-top: 16rpx;
  margin-bottom: 8rpx;
  border-radius: 4rpx;
  background: rgba(255, 255, 255, 0.16);
}

.uv-close {
  position: absolute;
  top: 16rpx;
  right: 24rpx;
  width: 56rpx;
  height: 56rpx;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.08);
  z-index: 1;
}

.uv-close-x {
  font-size: 36rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1;
}

/* ───────── 顶部标题区 (金色渐变) ───────── */
.uv-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8rpx;
  padding: 24rpx 32rpx 32rpx;
  background: linear-gradient(180deg, rgba(246, 196, 83, 0.18), rgba(246, 196, 83, 0));
}

.uv-crown {
  font-size: 64rpx;
  line-height: 1;
}

.uv-title {
  font-size: 40rpx;
  font-weight: 700;
  background: linear-gradient(135deg, #f6c453, #d97706);
  background-clip: text;
  -webkit-background-clip: text;
  /* H5 + App 显金色渐变文字; MP-WEIXIN background-clip:text 不支持, 退化为纯文字色 */
  color: #f6c453;
}

.uv-subtitle {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  opacity: 0.85;
  text-align: center;
  line-height: 1.5;
}

/* ───────── 配额尾巴 ───────── */
.uv-quota {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  margin: 0 32rpx 16rpx;
  padding: 20rpx 24rpx;
  background: rgba(246, 196, 83, 0.06);
  border: 1rpx solid rgba(246, 196, 83, 0.2);
  border-radius: 16rpx;
}

.uv-quota-head {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}

.uv-quota-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

.uv-quota-value {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
}

.uv-quota-used {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-accent, #f6c453);
}

.uv-quota-sep {
  color: var(--color-text-muted, #94a3b8);
}

.uv-progress-track {
  width: 100%;
  height: 12rpx;
  background: rgba(255, 255, 255, 0.06);
  border-radius: 6rpx;
  overflow: hidden;
}

.uv-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, #f6c453, #d97706);
  border-radius: 6rpx;
  transition: width 0.32s ease-out;
}

.uv-quota-retry {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
}

/* ───────── 权益清单 ───────── */
.uv-body {
  flex: 1;
  min-height: 360rpx;
  max-height: 56vh;
  padding: 0 32rpx;
}

.uv-perks {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.uv-perk {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
  padding: 20rpx 24rpx;
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 14rpx;
}

.uv-perk-hl {
  background: rgba(246, 196, 83, 0.08);
  border-color: rgba(246, 196, 83, 0.32);
}

.uv-perk-emoji {
  font-size: 40rpx;
  line-height: 1;
  flex-shrink: 0;
}

.uv-perk-text {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  flex: 1;
  overflow: hidden;
}

.uv-perk-title {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.uv-perk-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
}

.uv-perk-tag {
  flex-shrink: 0;
  padding: 4rpx 12rpx;
  background: linear-gradient(135deg, #f6c453, #d97706);
  color: #1a1305;
  font-size: 20rpx;
  font-weight: 700;
  border-radius: 999rpx;
}

.uv-legal {
  display: block;
  margin: 24rpx 0 16rpx;
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
  opacity: 0.7;
}

/* ───────── 底部双 CTA ───────── */
.uv-actions {
  display: flex;
  flex-direction: row;
  gap: 16rpx;
  padding: 16rpx 32rpx 8rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
}

.uv-btn {
  flex: 1;
  padding: 22rpx 0;
  text-align: center;
  border-radius: 12rpx;
}

.uv-btn-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid rgba(255, 255, 255, 0.12);
}

.uv-btn-primary {
  background: linear-gradient(135deg, #f6c453, #d97706);
  /* 微金属感: 内层高光 */
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.32);
}

.uv-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #1a1305;
}

.uv-btn-text-ghost {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}

/* iPhone 底栏安全区兜底 */
.uv-safe {
  padding-bottom: env(safe-area-inset-bottom);
}
</style>
