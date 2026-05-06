<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 登录页 (FE-001, 依赖 BE-002 + BE-005).
 *
 * 双方式:
 * 1. 手机号 + OTP (H5 / 小程序 / App 全平台)
 * 2. 微信一键登录 (仅 MP-WEIXIN; 调 ``uni.login`` 拿 code → 后端 ``code2Session``)
 *
 * UX 要点:
 * - 验证码 60s 倒计时; 后端也限流 (60s/手机号), 前端只做镜像
 * - 协议勾选必勾才能点登录 (合规要求, spec/06 §法律隔离)
 * - 错误 toast 走 ``parseAuthError`` 拿 ``detail.code`` 给"重试"语义判断
 * - 登录成功 ``uni.reLaunch`` 回首页, 不留返回栈 (防止用户后退到登录页又看到 loading 态)
 *
 * 不在这里做的:
 * - Pinia store / 拦截器 (FE-002)
 * - silent refresh (FE-002)
 * - 拒绝勾选时的"协议预读" 弹窗 (FE-003 个人中心也得统一处理, 这里先不重复)
 */

import { computed, onUnmounted, reactive, ref } from 'vue'

import {
  loginPhone,
  loginWechatMp,
  loginWithPassword,
  parseAuthError,
  sendOtp,
} from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

/**
 * BUG-S9-001: 密码登录入口同 OTP 同级 tab.
 * - ``password``: 手机/邮箱+密码 (主流方式, 默认)
 * - ``phone``: 手机+OTP (兼容老用户 / "忘记密码"找回兜底)
 * - ``wechat``: 微信一键 (仅 MP-WEIXIN)
 *
 * Sprint 1 默认是 ``phone``; Sprint 9 起改 ``password`` 优先 (用户主流登录方式),
 * 但 OTP 仍保留作"忘记密码"路径 — `forgotPassword` 链接直接 `tab = 'phone'`。
 */
type Tab = 'password' | 'phone' | 'wechat'

const tab = ref<Tab>('password')

const form = reactive({
  phone: '',
  code: '',
  // BUG-S9-001 密码登录: identifier 自动识别 phone vs email
  identifier: '',
  password: '',
})

const showPassword = ref(false)

const agreed = ref(false)
const loading = ref(false)
const wechatLoading = ref(false)

const otpCountdown = ref(0)
let countdownTimer: ReturnType<typeof setInterval> | null = null

// 仅 MP-WEIXIN 显示微信登录 Tab; H5 / App 端用手机号即可
const showWechatTab = computed(() => {
  // #ifdef MP-WEIXIN
  return true
  // #endif
  // #ifndef MP-WEIXIN
  return false
  // #endif
})

/** 后端要求 ``min_length=8`` 且包含数字; 前端松一点, 让后端归一 +86 */
const phoneValid = computed(() => {
  const p = form.phone.trim()
  return p.length >= 8 && /\d/.test(p)
})

const codeValid = computed(() => /^\d{6}$/.test(form.code))

const canSendOtp = computed(
  () => phoneValid.value && otpCountdown.value === 0 && !loading.value,
)

const canSubmit = computed(
  () => phoneValid.value && codeValid.value && agreed.value && !loading.value,
)

/** BUG-S9-001 密码登录字段校验 — identifier 必须 ≥ 4 字符 (phone 最短或 email).
 * 实际格式校验在后端做 (这里只挡明显空 / 太短, 减少 422 噪音). */
const identifierValid = computed(() => form.identifier.trim().length >= 4)

/** BUG-S9-001 后端策略: 6-32 字符 + 至少含一位数字; 前端镜像同款规则 */
const passwordValid = computed(() => {
  const p = form.password
  return p.length >= 6 && p.length <= 32 && /\d/.test(p)
})

const canSubmitPassword = computed(
  () =>
    identifierValid.value &&
    passwordValid.value &&
    agreed.value &&
    !loading.value,
)

function showError(msg: string) {
  uni.showToast({ title: msg, icon: 'none', duration: 2000 })
}

function startCountdown(seconds: number) {
  otpCountdown.value = seconds
  if (countdownTimer) clearInterval(countdownTimer)
  countdownTimer = setInterval(() => {
    if (otpCountdown.value <= 1) {
      otpCountdown.value = 0
      if (countdownTimer) clearInterval(countdownTimer)
      countdownTimer = null
    } else {
      otpCountdown.value -= 1
    }
  }, 1000)
}

async function handleSendOtp() {
  if (!canSendOtp.value) {
    if (!phoneValid.value) showError('请输入正确的手机号')
    return
  }
  loading.value = true
  try {
    const resp = await sendOtp({ phone: form.phone.trim() })
    uni.showToast({
      title: `验证码已发送至 ${resp.masked_phone}`,
      icon: 'none',
    })
    startCountdown(60)
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'otp_send_rate_limited') {
      showError('60 秒内只能获取一次验证码')
      // 后端拒绝时, 倒计时保险也拉起来, 防止前端时钟漂移导致用户狂点
      startCountdown(60)
    } else if (code === 'phone_format_invalid') {
      showError('手机号格式不正确')
    } else {
      showError(message || '发送失败, 请稍后再试')
    }
  } finally {
    loading.value = false
  }
}

function gotoHome() {
  // FE-S6-001: tabBar 已开启, 5 tab 都用 switchTab; 失败兜底 reLaunch
  // (例如未来某 tab 被临时下线 / pages.json 没注册时)
  uni.switchTab({
    url: '/pages/index/index',
    fail: () => uni.reLaunch({ url: '/pages/index/index' }),
  })
}

/**
 * BUG-S9-002 + Q4: 登录成功后的跳转决策.
 * - profile_complete=false (老 OTP 用户没设密码 / 微信用户没补手机邮箱) → reLaunch 完善资料页
 * - profile_complete=true (或字段缺失向下兼容) → 走 tab home
 *
 * profile_complete 字段由 BE ``UserPublic._derive_has_flags`` 派生:
 * has_password AND (has_phone OR has_email). 见 schemas/auth.py.
 */
function gotoNext(resp: { user: { profile_complete?: boolean } }) {
  if (resp.user.profile_complete === false) {
    uni.reLaunch({ url: '/pages/auth/profile-complete' })
  } else {
    gotoHome()
  }
}

async function handlePasswordLogin() {
  if (!canSubmitPassword.value) {
    if (!agreed.value) {
      showError('请先勾选并同意协议')
    } else if (!identifierValid.value) {
      showError('请输入手机号或邮箱')
    } else if (!passwordValid.value) {
      showError('密码 6-32 位且至少含一个数字')
    }
    return
  }
  loading.value = true
  try {
    const resp = await loginWithPassword({
      identifier: form.identifier.trim(),
      password: form.password,
    })
    auth.setSession(resp)
    uni.showToast({
      title: resp.is_new_user ? '欢迎加入新股智汇' : '登录成功',
      icon: 'success',
    })
    setTimeout(() => gotoNext(resp), 600)
  } catch (e) {
    const { code, message } = parseAuthError(e)
    // BUG-S9-001 后端统一 ``invalid_credentials`` 防 enumeration; FE 同款处理
    if (code === 'invalid_credentials') {
      showError('账号或密码错误, 请检查后重试')
      // 错过密码的安全反应: 清空密码框 (仿微信登录失败行为)
      form.password = ''
    } else if (code === 'password_login_rate_limited' || code === 'http_429') {
      showError('错误次数过多, 请 5 分钟后再试')
    } else if (code === 'identifier_format_invalid') {
      showError('账号格式错误, 请输入手机号或邮箱')
    } else {
      showError(message || '登录失败')
    }
  } finally {
    loading.value = false
  }
}

function gotoRegister() {
  // BUG-S9-001 跳独立注册页 (FE pages/auth/register.vue);
  // 注册成功 store.setSession 后由 register 页自己 reLaunch /pages/index/index
  uni.navigateTo({ url: '/pages/auth/register' })
}

function forgotPassword() {
  // BUG-S9-001 拍板 Q4: OTP 路径作"忘记密码"找回兜底.
  // FE 当前简化: 切到 OTP tab 让用户用验证码登录, 进入后引导设置新密码 (Step 3 of profile-complete).
  // 后续可加独立 reset-password 流程 (输 phone/email → 收 OTP → 设新密码 → 不登录), 待 Sprint 10+.
  tab.value = 'phone'
  uni.showToast({
    title: '请用验证码登录, 登录后可重设密码',
    icon: 'none',
    duration: 2500,
  })
}

async function handlePhoneLogin() {
  if (!canSubmit.value) {
    if (!agreed.value) {
      showError('请先勾选并同意协议')
    } else if (!codeValid.value) {
      showError('请输入 6 位数字验证码')
    } else if (!phoneValid.value) {
      showError('请输入正确的手机号')
    }
    return
  }
  loading.value = true
  try {
    const resp = await loginPhone({
      phone: form.phone.trim(),
      code: form.code.trim(),
    })
    auth.setSession(resp)
    uni.showToast({
      title: resp.is_new_user ? '欢迎加入新股智汇' : '登录成功',
      icon: 'success',
    })
    setTimeout(() => gotoNext(resp), 600)
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'otp_invalid') {
      showError('验证码错误')
      form.code = ''
    } else if (code === 'otp_expired') {
      showError('验证码已过期, 请重新获取')
      form.code = ''
      otpCountdown.value = 0
    } else if (code === 'otp_verify_rate_limited') {
      showError('验证次数过多, 请 5 分钟后再试')
    } else {
      showError(message || '登录失败')
    }
  } finally {
    loading.value = false
  }
}

// #ifdef MP-WEIXIN
async function handleWechatLogin() {
  if (!agreed.value) {
    showError('请先勾选并同意协议')
    return
  }
  wechatLoading.value = true
  try {
    // uni.login 拿 5 分钟一次性 code; 后端走 code2Session 换 openid/unionid.
    // 内联返回类型, 不依赖 @dcloudio/types 的 UniNamespace.LoginRes namespace,
    // 减少版本飘移导致的 TS 解析失败 (本工程 @dcloudio/* 版本已被 yank, 待统一升级)
    const loginRes = await new Promise<{ code?: string; errMsg?: string }>(
      (resolve, reject) => {
        uni.login({
          provider: 'weixin',
          success: (res) => resolve(res as { code?: string; errMsg?: string }),
          fail: reject,
        })
      },
    )
    if (!loginRes.code) {
      throw new Error('微信授权失败: 未获取到 code')
    }
    const resp = await loginWechatMp({ code: loginRes.code })
    auth.setSession(resp)
    uni.showToast({
      title: resp.is_new_user ? '欢迎加入新股智汇' : '登录成功',
      icon: 'success',
    })
    setTimeout(() => gotoNext(resp), 600)
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'wechat_code_invalid') {
      showError('微信授权码已失效, 请重试')
    } else if (code === 'wechat_mp_disabled') {
      showError('微信小程序登录暂未开放, 请用手机号登录')
      tab.value = 'phone'
    } else if (code === 'wechat_upstream_error') {
      showError('微信服务暂时不可用, 请稍后再试')
    } else {
      showError(message || '微信登录失败')
    }
  } finally {
    wechatLoading.value = false
  }
}
// #endif

function openAgreement(kind: 'tos' | 'privacy' | 'disclaimer') {
  const titles = {
    tos: '用户协议',
    privacy: '隐私政策',
    disclaimer: '免责声明',
  }
  uni.showModal({
    title: titles[kind],
    content:
      '完整文本请前往「设置 → 法律条款」查看. \n\n本应用为信息聚合工具, 不构成投资 / 税务 / 法律建议. 投资有风险, 入市需谨慎。',
    showCancel: false,
    confirmText: '我知道了',
  })
}

onUnmounted(() => {
  if (countdownTimer) clearInterval(countdownTimer)
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view class="hero">
      <text class="hero-logo">新股智汇</text>
      <text class="hero-sub">港 A 股打新 · AI 分析 · 跨境合规</text>
    </view>

    <view class="card">
      <!-- BUG-S9-001 三 tab 切换: 密码 (主) / OTP / 微信 (mp-only).
           tab 顺序按用户主流频次: 密码 (90%) > OTP (~5% 找回密码) > 微信 (mp 独有 5%) -->
      <view class="tabs">
        <view
          :class="['tab', tab === 'password' && 'tab-active']"
          @tap="tab = 'password'"
        >
          密码登录
        </view>
        <view
          :class="['tab', tab === 'phone' && 'tab-active']"
          @tap="tab = 'phone'"
        >
          短信验证码
        </view>
        <view
          v-if="showWechatTab"
          :class="['tab', tab === 'wechat' && 'tab-active']"
          @tap="tab = 'wechat'"
        >
          微信
        </view>
      </view>

      <!-- BUG-S9-001 密码登录 -->
      <view v-if="tab === 'password'" class="form">
        <view class="field">
          <text class="label">账号</text>
          <input
            v-model="form.identifier"
            class="input"
            type="text"
            maxlength="254"
            placeholder="手机号 / 邮箱"
            placeholder-class="input-placeholder"
          />
        </view>

        <view class="field">
          <text class="label">密码</text>
          <view class="row">
            <input
              v-model="form.password"
              class="input input-flex"
              :password="!showPassword"
              maxlength="32"
              placeholder="6-32 位含数字"
              placeholder-class="input-placeholder"
            />
            <view
              class="password-toggle"
              @tap="showPassword = !showPassword"
            >
              {{ showPassword ? '隐藏' : '显示' }}
            </view>
          </view>
        </view>

        <view class="agree-row" @tap="agreed = !agreed">
          <view :class="['checkbox', agreed && 'checkbox-on']">
            <text v-if="agreed" class="check">✓</text>
          </view>
          <view class="agree-text">
            <text>我已阅读并同意</text>
            <text class="link" @tap.stop="openAgreement('tos')">《用户协议》</text>
            <text class="link" @tap.stop="openAgreement('privacy')">《隐私政策》</text>
            <text class="link" @tap.stop="openAgreement('disclaimer')">《免责声明》</text>
          </view>
        </view>

        <view
          :class="['btn-primary', !canSubmitPassword && 'btn-disabled']"
          @tap="handlePasswordLogin"
        >
          {{ loading ? '登录中…' : '登录' }}
        </view>

        <view class="auth-footer-links">
          <text class="link" @tap="forgotPassword">忘记密码?</text>
          <text class="divider">·</text>
          <text class="link" @tap="gotoRegister">立即注册</text>
        </view>
      </view>

      <!-- 手机号 OTP 登录 (兼容老用户 / 找回密码兜底) -->
      <view v-if="tab === 'phone'" class="form">
        <view class="field">
          <text class="label">手机号</text>
          <input
            v-model="form.phone"
            class="input"
            type="number"
            maxlength="20"
            placeholder="请输入手机号 (国内 11 位 / +区号)"
            placeholder-class="input-placeholder"
          />
        </view>

        <view class="field">
          <text class="label">验证码</text>
          <view class="row">
            <input
              v-model="form.code"
              class="input input-flex"
              type="number"
              maxlength="6"
              placeholder="6 位数字验证码"
              placeholder-class="input-placeholder"
            />
            <view
              :class="['otp-btn', !canSendOtp && 'otp-btn-disabled']"
              @tap="handleSendOtp"
            >
              {{ otpCountdown > 0 ? `${otpCountdown}s 后重发` : '获取验证码' }}
            </view>
          </view>
        </view>

        <!-- QA-S5-001 BC-3: 协议勾选挪到登录按钮"上方"紧贴, 防小屏被推出可见区 -->
        <view class="agree-row" @tap="agreed = !agreed">
          <view :class="['checkbox', agreed && 'checkbox-on']">
            <text v-if="agreed" class="check">✓</text>
          </view>
          <view class="agree-text">
            <text>我已阅读并同意</text>
            <text class="link" @tap.stop="openAgreement('tos')">《用户协议》</text>
            <text class="link" @tap.stop="openAgreement('privacy')">《隐私政策》</text>
            <text class="link" @tap.stop="openAgreement('disclaimer')">《免责声明》</text>
          </view>
        </view>

        <view
          :class="['btn-primary', !canSubmit && 'btn-disabled']"
          @tap="handlePhoneLogin"
        >
          {{ loading ? '登录中…' : '登录 / 注册' }}
        </view>
      </view>

      <!-- 微信一键登录 -->
      <!-- #ifdef MP-WEIXIN -->
      <view v-if="tab === 'wechat'" class="form form-wechat">
        <view class="wechat-illustration">
          <text class="wechat-icon">微</text>
        </view>
        <text class="wechat-tip">使用微信账号一键登录</text>

        <!-- QA-S5-001 BC-3: 协议勾选与 phone form 同步, 共享 agreed ref -->
        <view class="agree-row agree-row-center" @tap="agreed = !agreed">
          <view :class="['checkbox', agreed && 'checkbox-on']">
            <text v-if="agreed" class="check">✓</text>
          </view>
          <view class="agree-text">
            <text>我已阅读并同意</text>
            <text class="link" @tap.stop="openAgreement('tos')">《用户协议》</text>
            <text class="link" @tap.stop="openAgreement('privacy')">《隐私政策》</text>
            <text class="link" @tap.stop="openAgreement('disclaimer')">《免责声明》</text>
          </view>
        </view>

        <view
          :class="['btn-primary', 'btn-wechat', wechatLoading && 'btn-disabled']"
          @tap="handleWechatLogin"
        >
          {{ wechatLoading ? '登录中…' : '微信一键登录' }}
        </view>
        <view class="switch-link" @tap="tab = 'phone'">改用手机号登录</view>
      </view>
      <!-- #endif -->
    </view>

    <!-- 合规 footer (仅风险提示; 协议勾选已挪到登录按钮上方) -->
    <view class="footer">
      <text class="risk">
        投资有风险, 入市需谨慎. 本应用为信息聚合工具, 不构成投资 / 税务 / 法律建议.
      </text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 80rpx 48rpx 48rpx;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
}

.hero {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 64rpx;

  .hero-logo {
    font-size: 56rpx;
    font-weight: 700;
    background: linear-gradient(135deg, #4f8bff 0%, #f6c453 100%);
    -webkit-background-clip: text;
    background-clip: text;
    color: transparent;
    margin-bottom: 16rpx;
  }

  .hero-sub {
    font-size: 26rpx;
    color: var(--color-text-muted, #94a3b8);
  }
}

.card {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 48rpx 36rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}

.tabs {
  display: flex;
  margin-bottom: 32rpx;
  border-bottom: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));

  .tab {
    flex: 1;
    text-align: center;
    padding: 24rpx 0;
    font-size: 28rpx;
    color: var(--color-text-muted, #94a3b8);
    position: relative;
  }

  .tab-active {
    color: var(--color-text, #e2e8f0);
    font-weight: 600;

    &::after {
      content: '';
      position: absolute;
      bottom: 0;
      left: 50%;
      transform: translateX(-50%);
      width: 64rpx;
      height: 4rpx;
      border-radius: 2rpx;
      background: var(--color-primary, #4f8bff);
    }
  }
}

.form {
  display: flex;
  flex-direction: column;
  gap: 28rpx;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 12rpx;

  .label {
    font-size: 24rpx;
    color: var(--color-text-muted, #94a3b8);
  }
}

.row {
  display: flex;
  align-items: center;
  gap: 16rpx;
}

.input {
  height: 88rpx;
  padding: 0 24rpx;
  font-size: 30rpx;
  color: var(--color-text, #e2e8f0);
  background: var(--color-surface-elevated, #1a2238);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 16rpx;
}

.input-flex {
  flex: 1;
}

.input-placeholder {
  color: rgba(148, 163, 184, 0.5);
}

.otp-btn {
  flex: 0 0 auto;
  padding: 0 28rpx;
  height: 88rpx;
  line-height: 88rpx;
  font-size: 26rpx;
  color: var(--color-primary, #4f8bff);
  background: rgba(79, 139, 255, 0.08);
  border-radius: 16rpx;
  border: 1rpx solid rgba(79, 139, 255, 0.3);
  text-align: center;
  white-space: nowrap;
}

.otp-btn-disabled {
  color: var(--color-text-muted, #94a3b8);
  background: rgba(148, 163, 184, 0.08);
  border-color: rgba(148, 163, 184, 0.2);
}

/* BUG-S9-001 密码 显示/隐藏 toggle, 与 .otp-btn 同款样式 */
.password-toggle {
  flex: 0 0 auto;
  padding: 0 24rpx;
  height: 88rpx;
  line-height: 88rpx;
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  background: rgba(148, 163, 184, 0.08);
  border-radius: 16rpx;
  border: 1rpx solid rgba(148, 163, 184, 0.2);
  text-align: center;
  white-space: nowrap;
}

/* BUG-S9-001 登录按钮下方"忘记密码 · 立即注册"链接行 */
.auth-footer-links {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 16rpx;
  margin-top: 16rpx;

  .link {
    font-size: 24rpx;
  }

  .divider {
    color: var(--color-text-muted, #94a3b8);
    opacity: 0.5;
  }
}

.btn-primary {
  height: 96rpx;
  line-height: 96rpx;
  text-align: center;
  font-size: 30rpx;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(135deg, #4f8bff 0%, #6b9aff 100%);
  border-radius: 16rpx;
  margin-top: 8rpx;
  box-shadow: 0 8rpx 24rpx rgba(79, 139, 255, 0.25);
}

.btn-disabled {
  background: rgba(148, 163, 184, 0.18);
  color: rgba(255, 255, 255, 0.5);
  box-shadow: none;
}

.btn-wechat {
  background: linear-gradient(135deg, #1aad19 0%, #2dba2c 100%);
  box-shadow: 0 8rpx 24rpx rgba(26, 173, 25, 0.25);
}

.form-wechat {
  align-items: center;
  padding: 24rpx 0;

  .wechat-illustration {
    width: 144rpx;
    height: 144rpx;
    border-radius: 50%;
    background: rgba(26, 173, 25, 0.15);
    display: flex;
    align-items: center;
    justify-content: center;
    margin-bottom: 24rpx;
  }

  .wechat-icon {
    font-size: 60rpx;
    font-weight: 700;
    color: #1aad19;
  }

  .wechat-tip {
    font-size: 26rpx;
    color: var(--color-text-muted, #94a3b8);
    margin-bottom: 32rpx;
  }

  .btn-wechat {
    width: 100%;
  }

  .switch-link {
    margin-top: 24rpx;
    font-size: 26rpx;
    color: var(--color-primary, #4f8bff);
  }
}

/* QA-S5-001 BC-3: footer 不再放协议勾选, 只放风险提示;
 * margin-top: auto 让 risk 沉到 page 底部, 但 .agree-row 已脱离 footer
 * 紧贴登录按钮上方 (在 .card .form 末尾) — 小屏幕 form 高度永远在视口内,
 * 协议勾选不会再被挤到看不见
 */
.footer {
  margin-top: auto;
  padding-top: 32rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.agree-row {
  display: flex;
  align-items: flex-start;
  gap: 12rpx;
  /* 紧贴登录按钮上方; .form gap=28rpx 已经够空气, 这里不另设 margin */
  padding: 8rpx 4rpx;
}

/* 微信 tab 居中布局, 协议行也居中以视觉对齐 */
.agree-row-center {
  width: 100%;
  justify-content: center;
}

.checkbox {
  width: 32rpx;
  height: 32rpx;
  border-radius: 8rpx;
  border: 2rpx solid var(--color-text-muted, #94a3b8);
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 4rpx;
  flex-shrink: 0;
}

.checkbox-on {
  background: var(--color-primary, #4f8bff);
  border-color: var(--color-primary, #4f8bff);
}

.check {
  color: #fff;
  font-size: 22rpx;
  font-weight: 700;
}

.agree-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
  flex: 1;
}

.link {
  color: var(--color-primary, #4f8bff);
}

.risk {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.6;
  opacity: 0.7;
}
</style>
