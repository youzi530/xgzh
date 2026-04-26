<script setup lang="ts">
/**
 * 个人中心 (FE-003).
 *
 * 模块:
 * 1. 顶部资料卡: 头像 + 昵称 + region + 邀请码 (可点击复制)
 * 2. VIP 入口卡: 当前会员等级 + 升级按钮 (本期占位; 支付通道走后续 BE-XXX)
 * 3. 邀请绑定卡: 一次性绑定 referrer (BE-006); 已绑则灰态展示
 * 4. 设置区: 隐私协议 / 用户协议 / 免责声明 / 关于
 * 5. 退出登录按钮 (走 store.logout(), 然后 reLaunch 首页)
 *
 * 合规:
 * - 顶部固定"工具属性"角标 (spec/06 §法律隔离): 本平台为信息聚合工具, 不构成投资建议
 * - 协议条目使用与 login.vue 一致的占位 modal, 上线前替换为 webview
 *
 * 鉴权边界:
 * - onShow 时若 ``store.loggedIn === false`` 直接 ``uni.reLaunch`` 回登录页
 *   (不能 navigateTo: 个人中心可能从 tabbar 跳来, 用户登出后回到这里再后退会闪)
 * - 拦截器在所有 API 401 时自动跳登录, 这里只处理"页面级冷启动 + 切换回前台"那一刻的兜底
 */

import { onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, reactive, ref } from 'vue'

import { bindInvite, parseInviteError } from '@/api/invite'
import { useAuthStore } from '@/stores/auth'

const KEY_BOUND_REFERRER = 'xgzh.invite.bound_referrer'

const authStore = useAuthStore()
const { user, loggedIn } = storeToRefs(authStore)

const inviteForm = reactive({ code: '' })
const inviteSubmitting = ref(false)
// 本地缓存"已绑定 referrer 邀请码"; 后端没单独 ``GET /me/referrer``,
// 用 storage 兜一份; 服务端是 source of truth, 重复绑定时拦截器会抛 400 ``invite_already_bound``
const boundReferrer = ref<string | null>(null)

const loggingOut = ref(false)

const nicknameInitial = computed(() => {
  const u = user.value
  if (!u) return '?'
  if (u.nickname && u.nickname.length > 0) return u.nickname.slice(0, 1)
  return u.invite_code.slice(0, 1)
})

const displayNickname = computed(() => user.value?.nickname || '未设置昵称')
const displayRegion = computed(() => {
  const r = user.value?.region ?? 'CN'
  // 区域 region 是后端给的 enum, 这里给本地化文案
  if (r === 'HK') return '香港'
  if (r === 'SG') return '新加坡'
  return '中国大陆'
})

function refreshAuthGate() {
  if (!loggedIn.value) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  const cached = uni.getStorageSync(KEY_BOUND_REFERRER) as string | ''
  boundReferrer.value = cached || null
}

function copyInviteCode() {
  const code = user.value?.invite_code
  if (!code) return
  uni.setClipboardData({
    data: code,
    success: () => uni.showToast({ title: '邀请码已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

async function handleBindInvite() {
  const raw = inviteForm.code.trim().toUpperCase()
  if (raw.length < 4 || raw.length > 16) {
    uni.showToast({ title: '邀请码长度 4-16 位', icon: 'none' })
    return
  }
  if (raw === user.value?.invite_code) {
    uni.showToast({ title: '不能绑定自己的邀请码', icon: 'none' })
    return
  }
  inviteSubmitting.value = true
  try {
    const resp = await bindInvite({ code: raw })
    boundReferrer.value = resp.referrer_invite_code
    uni.setStorageSync(KEY_BOUND_REFERRER, resp.referrer_invite_code)
    inviteForm.code = ''
    uni.showToast({ title: '绑定成功', icon: 'success' })
  } catch (err) {
    const { code, message } = parseInviteError(err)
    // 7 类错误差异化 UX
    switch (code) {
      case 'invite_code_not_found':
        uni.showToast({ title: '邀请码不存在', icon: 'none' })
        break
      case 'invite_self_binding':
        uni.showToast({ title: '不能绑定自己的邀请码', icon: 'none' })
        break
      case 'invite_already_bound':
        // 服务端说已绑过, 但前端 storage 没缓存 → 用户清过 storage 或换设备登录了
        // 此时把 input 置灰, 提示"已绑定但本机未缓存", 不阻塞用户继续使用
        boundReferrer.value = '已绑定'
        uni.setStorageSync(KEY_BOUND_REFERRER, '已绑定')
        uni.showToast({ title: '你已绑定过邀请人, 不可更改', icon: 'none' })
        break
      case 'invite_code_inactive':
        uni.showToast({ title: '邀请码已被禁用', icon: 'none' })
        break
      case 'invite_code_expired':
        uni.showToast({ title: '邀请码已过期', icon: 'none' })
        break
      case 'invite_code_exhausted':
        uni.showToast({ title: '邀请码使用次数已满', icon: 'none' })
        break
      case 'invite_code_not_personal':
        uni.showToast({ title: '该码不可用作邀请人', icon: 'none' })
        break
      case 'rate_limit_exceeded':
        uni.showToast({ title: '尝试过于频繁, 请稍后再试', icon: 'none' })
        break
      default:
        uni.showToast({ title: message || '绑定失败', icon: 'none' })
    }
  } finally {
    inviteSubmitting.value = false
  }
}

function gotoVip() {
  uni.showModal({
    title: '升级 VIP',
    content:
      '会员特权:\n· AI 深度诊断 (现限免)\n· 无限自选 + 提醒\n· 历史打新数据库\n· CRS 报税向导\n\n支付通道开发中, 敬请期待。',
    showCancel: false,
    confirmText: '我知道了',
  })
}

function openLegal(kind: 'tos' | 'privacy' | 'disclaimer' | 'about') {
  const map: Record<typeof kind, { title: string; content: string }> = {
    tos: {
      title: '用户协议',
      content: '本应用仅作为信息聚合工具, 不构成任何投资 / 税务 / 法律建议. 完整文本待上架前正式发布。',
    },
    privacy: {
      title: '隐私政策',
      content: '我们仅收集必要的登录信息 (手机号 / 微信 OpenID), 不收集投资账户. 完整文本待上架前发布。',
    },
    disclaimer: {
      title: '免责声明',
      content: 'AI 分析结果仅供参考, 不构成投资建议; 数据来源已在各页底部标注. 投资有风险, 入市需谨慎。',
    },
    about: {
      title: '关于',
      content: '新股智汇 XGZH · IPO Agent\n\n版本: MVP\n用途: 港 A 打新信息聚合 + AI 分析\n\n如需反馈请发邮件至 contact@example.com',
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

async function handleLogout() {
  const confirm = await new Promise<boolean>((resolve) => {
    uni.showModal({
      title: '确认退出',
      content: '退出后需要重新登录才能使用全部功能',
      cancelText: '取消',
      confirmText: '退出',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    })
  })
  if (!confirm) return
  loggingOut.value = true
  try {
    await authStore.logout()
    // store.logout 内部已 clearSession + (尽力调) 后端 logout API
    uni.removeStorageSync(KEY_BOUND_REFERRER)
    uni.reLaunch({ url: '/pages/index/index' })
  } finally {
    loggingOut.value = false
  }
}

onShow(() => {
  refreshAuthGate()
})
</script>

<template>
  <view class="page">
    <view class="legal-banner">
      <text class="legal-banner-text">本平台为信息聚合工具, 不构成投资建议</text>
    </view>

    <view v-if="user" class="profile-card">
      <view class="avatar">
        <text class="avatar-text">{{ nicknameInitial }}</text>
      </view>
      <view class="profile-info">
        <text class="profile-nickname">{{ displayNickname }}</text>
        <text class="profile-region">{{ displayRegion }}</text>
        <view class="invite-row" @tap="copyInviteCode">
          <text class="invite-label">我的邀请码</text>
          <text class="invite-code">{{ user.invite_code }}</text>
          <text class="invite-copy">复制</text>
        </view>
      </view>
    </view>

    <view class="vip-card" @tap="gotoVip">
      <view class="vip-left">
        <text class="vip-tag">免费会员</text>
        <text class="vip-desc">升级 VIP 解锁 AI 深度诊断 / 历史数据 / 提醒</text>
      </view>
      <view class="vip-cta">
        <text>升级</text>
      </view>
    </view>

    <view class="section">
      <view class="section-header">
        <text class="section-title">绑定邀请人</text>
        <text class="section-subtitle">仅可绑定一次, 不可更改</text>
      </view>
      <view v-if="boundReferrer" class="invite-bound">
        <text class="invite-bound-label">已绑定邀请人</text>
        <text class="invite-bound-code">{{ boundReferrer }}</text>
      </view>
      <view v-else class="invite-form">
        <input
          v-model="inviteForm.code"
          class="invite-input"
          maxlength="16"
          placeholder="请输入邀请人邀请码"
          placeholder-class="invite-placeholder"
        />
        <button
          class="invite-submit"
          :class="{ 'invite-submit-disabled': inviteSubmitting }"
          :disabled="inviteSubmitting"
          @tap="handleBindInvite"
        >
          {{ inviteSubmitting ? '提交中...' : '绑定' }}
        </button>
      </view>
    </view>

    <view class="section">
      <view class="section-header">
        <text class="section-title">设置 / 关于</text>
      </view>
      <view class="link-list">
        <view class="link-item" @tap="openLegal('tos')">
          <text class="link-text">用户协议</text>
          <text class="link-arrow">›</text>
        </view>
        <view class="link-item" @tap="openLegal('privacy')">
          <text class="link-text">隐私政策</text>
          <text class="link-arrow">›</text>
        </view>
        <view class="link-item" @tap="openLegal('disclaimer')">
          <text class="link-text">免责声明</text>
          <text class="link-arrow">›</text>
        </view>
        <view class="link-item" @tap="openLegal('about')">
          <text class="link-text">关于新股智汇</text>
          <text class="link-arrow">›</text>
        </view>
      </view>
    </view>

    <button
      class="logout-btn"
      :class="{ 'logout-btn-disabled': loggingOut }"
      :disabled="loggingOut"
      @tap="handleLogout"
    >
      {{ loggingOut ? '退出中...' : '退出登录' }}
    </button>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.legal-banner {
  background: rgba(246, 196, 83, 0.08);
  border: 1rpx solid rgba(246, 196, 83, 0.25);
  border-radius: 12rpx;
  padding: 16rpx 24rpx;
}
.legal-banner-text {
  font-size: 22rpx;
  color: #f6c453;
}

.profile-card {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 32rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  display: flex;
  align-items: center;
  gap: 28rpx;
}
.avatar {
  width: 112rpx;
  height: 112rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #f6c453);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.avatar-text {
  font-size: 48rpx;
  font-weight: 700;
  color: #fff;
}
.profile-info {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.profile-nickname {
  font-size: 36rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.profile-region {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.invite-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
  margin-top: 8rpx;
  padding: 12rpx 16rpx;
  background: rgba(79, 139, 255, 0.08);
  border-radius: 12rpx;
}
.invite-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.invite-code {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-primary, #4f8bff);
  letter-spacing: 2rpx;
  flex: 1;
}
.invite-copy {
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
}

.vip-card {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.15), rgba(79, 139, 255, 0.1));
  border: 1rpx solid rgba(246, 196, 83, 0.35);
  border-radius: 24rpx;
  padding: 32rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24rpx;
}
.vip-left {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.vip-tag {
  font-size: 28rpx;
  font-weight: 700;
  color: #f6c453;
}
.vip-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.vip-cta {
  padding: 16rpx 32rpx;
  border-radius: 999rpx;
  background: #f6c453;
  color: #0b1220;
  font-size: 26rpx;
  font-weight: 700;
}

.section {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 28rpx 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.section-header {
  margin-bottom: 20rpx;
}
.section-title {
  display: block;
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.section-subtitle {
  display: block;
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.invite-bound {
  background: rgba(255, 255, 255, 0.03);
  border-radius: 12rpx;
  padding: 24rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.invite-bound-label {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.invite-bound-code {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
  letter-spacing: 2rpx;
}

.invite-form {
  display: flex;
  gap: 16rpx;
}
.invite-input {
  flex: 1;
  height: 80rpx;
  padding: 0 24rpx;
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 12rpx;
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
  letter-spacing: 2rpx;
  text-transform: uppercase;
}
.invite-placeholder {
  color: var(--color-text-muted, #94a3b8);
  font-size: 26rpx;
  letter-spacing: 0;
}
.invite-submit {
  flex-shrink: 0;
  padding: 0 32rpx;
  height: 80rpx;
  line-height: 80rpx;
  border-radius: 12rpx;
  background: var(--color-primary, #4f8bff);
  color: #fff;
  font-size: 28rpx;
  font-weight: 600;
  border: none;
}
.invite-submit-disabled {
  opacity: 0.5;
}

.link-list {
  display: flex;
  flex-direction: column;
}
.link-item {
  height: 88rpx;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 4rpx;
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.04));

  &:last-child {
    border-bottom: none;
  }
}
.link-text {
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
}
.link-arrow {
  font-size: 36rpx;
  color: var(--color-text-muted, #94a3b8);
}

.logout-btn {
  margin-top: 8rpx;
  height: 88rpx;
  line-height: 88rpx;
  border-radius: 16rpx;
  background: rgba(239, 68, 68, 0.1);
  border: 1rpx solid rgba(239, 68, 68, 0.4);
  color: #ef4444;
  font-size: 28rpx;
  font-weight: 600;
}
.logout-btn-disabled {
  opacity: 0.6;
}
</style>
