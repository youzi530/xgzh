<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 注册页 (BUG-S9-001).
 *
 * 用户决策 Q1=B (手机+密码 / 邮箱+密码), Q3=A (6-32 含数字).
 *
 * UX:
 * - 顶部 segment 切换"手机号 / 邮箱" (默认手机号; 用户主流场景)
 * - 输入框跟着切换 (类型 / placeholder / 校验)
 * - 密码 + 确认密码 (二次输入降低 typo 概率)
 * - 邀请码可选, 占位"选填" (URL ``?invite=ABC`` 自动填入)
 * - 协议勾选必勾 (合规)
 * - 提交成功 → ``setSession`` → toast 欢迎 → switchTab 首页
 *
 * 错误码处理 (后端 BUG-S9-001):
 * - 400 ``identifier_format_invalid``: phone / email 格式错
 * - 409 ``phone_already_exists`` / ``email_already_exists``: 重复, 跳登录页
 * - 422: Pydantic 校验失败 (密码 / 邮箱)
 * - 429: 1 小时内同 identifier 注册 > 5 次
 *
 * 不在这里做的:
 * - 邮箱验证 link (MVP 信任邮箱, 后续 sprint 加)
 * - 密码强度可视化 meter (有数字 + 长度 OK 即放行, 不强迫用户混合大小写)
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'

import { parseAuthError, registerWithPassword } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

type IdMode = 'phone' | 'email'

const idMode = ref<IdMode>('phone')

const form = reactive({
  phone: '',
  email: '',
  password: '',
  passwordConfirm: '',
  inviteCode: '',
})

const showPassword = ref(false)
const agreed = ref(false)
const loading = ref(false)

// URL ``?invite=XXX`` 注入 (邀请人分享落地直达注册)
onLoad((opts) => {
  const inv = opts?.invite
  if (typeof inv === 'string' && inv.length > 0 && inv.length <= 32) {
    form.inviteCode = inv.trim().toUpperCase()
  }
})

const phoneValid = computed(() => {
  const p = form.phone.trim()
  return p.length >= 8 && /\d/.test(p)
})

const emailValid = computed(() => {
  const e = form.email.trim()
  return e.length >= 5 && e.includes('@') && e.includes('.')
})

const identifierValid = computed(() =>
  idMode.value === 'phone' ? phoneValid.value : emailValid.value,
)

/** BUG-S9-001 后端策略: 6-32 字符 + 至少含一位数字 */
const passwordValid = computed(() => {
  const p = form.password
  return p.length >= 6 && p.length <= 32 && /\d/.test(p)
})

const passwordConfirmValid = computed(
  () => form.password.length > 0 && form.password === form.passwordConfirm,
)

const canSubmit = computed(
  () =>
    identifierValid.value &&
    passwordValid.value &&
    passwordConfirmValid.value &&
    agreed.value &&
    !loading.value,
)

function showError(msg: string) {
  uni.showToast({ title: msg, icon: 'none', duration: 2000 })
}

function gotoHome() {
  uni.switchTab({
    url: '/pages/index/index',
    fail: () => uni.reLaunch({ url: '/pages/index/index' }),
  })
}

function gotoLogin() {
  // BUG-S9-001 注册页 → 登录页用 redirectTo (避免页面栈嵌套, login 是独立入口);
  // 失败兜底 navigateBack (login → register navigateTo 时返回更自然)
  uni.redirectTo({
    url: '/pages/auth/login',
    fail: () => uni.navigateBack({ delta: 1 }),
  })
}

async function handleRegister() {
  if (!canSubmit.value) {
    if (!agreed.value) {
      showError('请先勾选并同意协议')
    } else if (!identifierValid.value) {
      showError(idMode.value === 'phone' ? '请输入正确的手机号' : '请输入正确的邮箱')
    } else if (!passwordValid.value) {
      showError('密码 6-32 位且至少含一个数字')
    } else if (!passwordConfirmValid.value) {
      showError('两次输入的密码不一致')
    }
    return
  }
  loading.value = true
  try {
    const resp = await registerWithPassword({
      phone: idMode.value === 'phone' ? form.phone.trim() : null,
      email: idMode.value === 'email' ? form.email.trim() : null,
      password: form.password,
      invite_code: form.inviteCode.trim() || null,
    })
    auth.setSession(resp)
    uni.showToast({ title: '注册成功, 欢迎加入!', icon: 'success' })
    setTimeout(() => {
      // BUG-S9-002 注册成功后判断 profile_complete:
      // - true (phone+pwd 用户必然 complete): 直接进首页
      // - false (微信 OAuth 注册的用户走另一路径, 不会到这页): 兜底跳完善资料
      if (resp.user.profile_complete === false) {
        uni.reLaunch({ url: '/pages/auth/profile-complete' })
      } else {
        gotoHome()
      }
    }, 600)
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'phone_already_exists') {
      showError('该手机号已注册, 请直接登录')
      setTimeout(() => gotoLogin(), 1500)
    } else if (code === 'email_already_exists') {
      showError('该邮箱已注册, 请直接登录')
      setTimeout(() => gotoLogin(), 1500)
    } else if (code === 'identifier_format_invalid') {
      showError(message || '账号格式错误')
    } else if (code === 'http_429' || code === 'password_register_rate_limited') {
      showError('注册次数过多, 请稍后再试')
    } else if (code === 'http_422') {
      showError('密码格式不符合要求 (6-32 位含数字)')
    } else {
      showError(message || '注册失败, 请稍后重试')
    }
  } finally {
    loading.value = false
  }
}

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
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view class="hero">
      <text class="hero-logo">注册账号</text>
      <text class="hero-sub">手机号或邮箱二选一, 设置密码即可</text>
    </view>

    <view class="card">
      <!-- 手机号 / 邮箱 segment -->
      <view class="segments">
        <view
          :class="['segment', idMode === 'phone' && 'segment-active']"
          @tap="idMode = 'phone'"
        >
          手机号
        </view>
        <view
          :class="['segment', idMode === 'email' && 'segment-active']"
          @tap="idMode = 'email'"
        >
          邮箱
        </view>
      </view>

      <view class="form">
        <view v-if="idMode === 'phone'" class="field">
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

        <view v-else class="field">
          <text class="label">邮箱</text>
          <input
            v-model="form.email"
            class="input"
            type="text"
            maxlength="254"
            placeholder="example@domain.com"
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
              placeholder="6-32 位且至少含一个数字"
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

        <view class="field">
          <text class="label">确认密码</text>
          <input
            v-model="form.passwordConfirm"
            class="input"
            :password="!showPassword"
            maxlength="32"
            placeholder="再次输入密码"
            placeholder-class="input-placeholder"
          />
          <text
            v-if="form.passwordConfirm.length > 0 && !passwordConfirmValid"
            class="hint hint-error"
          >
            两次密码不一致
          </text>
        </view>

        <view class="field">
          <text class="label">邀请码 <text class="optional">(选填)</text></text>
          <input
            v-model="form.inviteCode"
            class="input"
            type="text"
            maxlength="32"
            placeholder="若有邀请人, 填写邀请码"
            placeholder-class="input-placeholder"
          />
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
          :class="['btn-primary', !canSubmit && 'btn-disabled']"
          @tap="handleRegister"
        >
          {{ loading ? '注册中…' : '完成注册' }}
        </view>

        <view class="auth-footer-links">
          <text>已有账号?</text>
          <text class="link" @tap="gotoLogin">直接登录</text>
        </view>
      </view>
    </view>

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

.segments {
  display: flex;
  gap: 16rpx;
  background: var(--color-surface-elevated, #1a2238);
  border-radius: 16rpx;
  padding: 8rpx;
  margin-bottom: 32rpx;

  .segment {
    flex: 1;
    text-align: center;
    padding: 20rpx 0;
    font-size: 28rpx;
    color: var(--color-text-muted, #94a3b8);
    border-radius: 12rpx;
    transition: background 0.2s, color 0.2s;
  }

  .segment-active {
    color: #fff;
    background: linear-gradient(135deg, #4f8bff 0%, #6b9aff 100%);
    font-weight: 600;
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

  .optional {
    color: var(--color-text-muted, #94a3b8);
    opacity: 0.6;
    font-weight: 400;
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

.hint {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  padding: 0 4rpx;

  &.hint-error {
    color: #f87171;
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

.auth-footer-links {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 12rpx;
  margin-top: 16rpx;
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);

  .link {
    color: var(--color-primary, #4f8bff);
  }
}

.agree-row {
  display: flex;
  align-items: flex-start;
  gap: 12rpx;
  padding: 8rpx 4rpx;
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

.footer {
  margin-top: auto;
  padding-top: 32rpx;
  display: flex;
  flex-direction: column;
  align-items: center;
}

.risk {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
  line-height: 1.6;
  opacity: 0.7;
}
</style>
