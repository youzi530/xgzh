<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 完善资料页 (BUG-S9-002 + Q4 老用户迁移).
 *
 * 触发场景:
 * - 微信注册的新用户 (没昵称 / 没手机/邮箱 / 没密码) → 登录后自动 reLaunch 这页
 * - 老 OTP 用户 (有手机, 没密码) → 登录后自动 reLaunch 这页 → 直接跳到 Step 3
 *
 * UX:
 * - 顶部进度条 ("步骤 1 / 2 / 3", 跟实际剩余步骤动态算)
 * - 三 step 顺序: 头像昵称 → 手机/邮箱 → 密码
 * - 已完成的 step 自动跳过, 不让用户重复填
 * - 底部"暂时跳过"链接 — 仅当本次已经至少前进过一步时显示, 兜底 PIPL 知情同意
 *   (用户拒填可以走 logout)
 *
 * 全部填完 → reLaunch /pages/index/index 进入正常流程.
 *
 * 与 register.vue 区别:
 * - register: 未登录态 → 创建账户
 * - profile-complete: 已登录态 → 补充字段 (PATCH /me + PUT /me/password +
 *   POST /me/avatar 三接口分别调)
 */

import { onShow } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, reactive, ref } from 'vue'

import {
  parseAuthError,
  setMyPassword,
  updateMe,
  uploadAvatar,
  type UserPublic,
} from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const { user, loggedIn } = storeToRefs(auth)

type StepKey = 'profile' | 'identifier' | 'password'

// --- step detection (跟随 user 字段动态算) -----------------------------

/** Step 1: 头像 + 昵称都没填全时需要 */
const needProfile = computed(
  () => !user.value?.nickname || !user.value?.avatar_url,
)

/** Step 2: 没绑手机 / 邮箱时需要 */
const needIdentifier = computed(
  () => !user.value?.has_phone && !user.value?.has_email,
)

/** Step 3: 没设密码时需要 */
const needPassword = computed(() => !user.value?.has_password)

const remainingSteps = computed<StepKey[]>(() => {
  const arr: StepKey[] = []
  if (needProfile.value) arr.push('profile')
  if (needIdentifier.value) arr.push('identifier')
  if (needPassword.value) arr.push('password')
  return arr
})

const totalSteps = computed(() => remainingSteps.value.length)

const currentStepIdx = ref(0)
const currentStep = computed<StepKey | null>(
  () => remainingSteps.value[currentStepIdx.value] ?? null,
)

const stepLabel = computed(() => {
  const k = currentStep.value
  if (k === 'profile') return '头像与昵称'
  if (k === 'identifier') return '手机号或邮箱'
  if (k === 'password') return '设置密码'
  return ''
})

// --- 表单字段 (3 step 共用一个 reactive) -------------------------------

const profileForm = reactive({
  // chooseAvatar 拿到的临时 path; 上传完后保存远端 URL 到 ``avatarUrl``
  tempAvatarPath: '',
  avatarUrl: '',
  nickname: '',
})

type IdMode = 'phone' | 'email'

const identifierForm = reactive({
  mode: 'phone' as IdMode,
  phone: '',
  email: '',
})

const passwordForm = reactive({
  password: '',
  passwordConfirm: '',
})

const showPassword = ref(false)
const submitting = ref(false)
const advanced = ref(false) // 是否点过"下一步" (用来决定底部"暂时跳过"显示)

// --- 校验 -----------------------------------------------------------

const profileValid = computed(() => {
  const nickOk = profileForm.nickname.trim().length >= 1
  // 头像可以使用之前已经存在的 avatarUrl (微信 chooseAvatar 后立即上传更新 user.avatar_url)
  // 或者刚 chooseAvatar 完正在等上传 (tempAvatarPath 非空); 校验时只要 user 里已经有 avatar_url 就算过
  const avatarOk = !!profileForm.avatarUrl || !!user.value?.avatar_url
  return nickOk && avatarOk
})

const phoneValid = computed(() => {
  const p = identifierForm.phone.trim()
  return p.length >= 8 && /\d/.test(p)
})

const emailValid = computed(() => {
  const e = identifierForm.email.trim()
  return e.length >= 5 && e.includes('@') && e.includes('.')
})

const identifierValid = computed(() =>
  identifierForm.mode === 'phone' ? phoneValid.value : emailValid.value,
)

const passwordValid = computed(() => {
  const p = passwordForm.password
  return p.length >= 6 && p.length <= 32 && /\d/.test(p)
})

const passwordConfirmValid = computed(
  () =>
    passwordForm.password.length > 0 &&
    passwordForm.password === passwordForm.passwordConfirm,
)

const passwordStepValid = computed(
  () => passwordValid.value && passwordConfirmValid.value,
)

const canAdvance = computed(() => {
  const k = currentStep.value
  if (k === 'profile') return profileValid.value
  if (k === 'identifier') return identifierValid.value
  if (k === 'password') return passwordStepValid.value
  return false
})

// --- 微信 chooseAvatar (mp-weixin only) ------------------------------

// #ifdef MP-WEIXIN
function onChooseAvatar(e: { detail: { avatarUrl: string } }) {
  const url = e.detail?.avatarUrl
  if (!url) return
  profileForm.tempAvatarPath = url
  // 立即上传 → 拿到 https URL 写本地, 之后跟着 nickname 一起 PATCH /me 落库
  ;(async () => {
    submitting.value = true
    try {
      const resp = await uploadAvatar(url)
      profileForm.avatarUrl = resp.user.avatar_url ?? ''
      // 后端已经 commit 了 avatar_url, 直接 setUser 让其它页面同步
      auth.setUser(resp.user)
      uni.showToast({ title: '头像已上传', icon: 'success' })
    } catch (err) {
      const { code, message } = parseAuthError(err)
      if (code === 'avatar_too_large') {
        uni.showToast({ title: '头像最大 2 MB', icon: 'none' })
      } else if (code === 'avatar_mime_unsupported') {
        uni.showToast({ title: '仅支持 jpg / png / webp', icon: 'none' })
      } else {
        uni.showToast({ title: message || '头像上传失败', icon: 'none' })
      }
      profileForm.tempAvatarPath = ''
    } finally {
      submitting.value = false
    }
  })()
}
// #endif

function onNicknameInput(e: unknown) {
  // <input type="nickname"> 微信会自动审核敏感词后回填.
  // mp-weixin: e = { detail: { value: string } }; H5: 普通 FocusEvent.
  // 用 unknown 绕过 vue-tsc 报"FocusEvent 与 detail 不兼容", 运行时只摘 mp 形态.
  const ev = e as { detail?: { value?: string } } | undefined
  const v = ev?.detail?.value
  if (typeof v === 'string') {
    profileForm.nickname = v.trim().slice(0, 20)
  }
}

// --- 提交各 step --------------------------------------------------------

async function submitProfileStep(): Promise<boolean> {
  // avatar 上传时已经更新了 user.avatar_url, 这里只 PATCH 昵称
  // (避免重复发 avatar_url —— 后端 PATCH 不会拒, 但请求 body 越小越好)
  try {
    const updated = await updateMe({ nickname: profileForm.nickname.trim() })
    auth.setUser(updated)
    return true
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'nickname_empty' || code === 'nickname_too_long') {
      uni.showToast({ title: message || '昵称格式不符', icon: 'none' })
    } else {
      uni.showToast({ title: message || '保存失败', icon: 'none' })
    }
    return false
  }
}

async function submitIdentifierStep(): Promise<boolean> {
  // BUG-S9-002 用 PATCH /me 写 email; phone 没在 PATCH /me 支持(暂不开放,
  // 防止微信用户随便改手机绑别人的号 — 手机绑定走独立的 verify-otp 流程,
  // 这里 mode='phone' 时引导用户改用 OTP 登录补绑)
  if (identifierForm.mode === 'phone') {
    uni.showModal({
      title: '手机号补绑',
      content:
        '请退出当前账户, 改用"短信验证码"登录补绑手机号, 或选择邮箱方式继续.',
      confirmText: '改用邮箱',
      cancelText: '退出登录',
      success: (res) => {
        if (res.confirm) {
          identifierForm.mode = 'email'
        } else {
          auth.logout()
          uni.reLaunch({ url: '/pages/auth/login' })
        }
      },
    })
    return false
  }
  try {
    const updated = await updateMe({ email: identifierForm.email.trim() })
    auth.setUser(updated)
    return true
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'email_already_exists') {
      uni.showToast({ title: '邮箱已被占用', icon: 'none' })
    } else if (code === 'email_format_invalid' || code === 'http_422') {
      uni.showToast({ title: '邮箱格式错', icon: 'none' })
    } else {
      uni.showToast({ title: message || '保存失败', icon: 'none' })
    }
    return false
  }
}

async function submitPasswordStep(): Promise<boolean> {
  try {
    // BUG-S9-001 老用户首次设密码 — has_password=false, current_password 不传
    const u: UserPublic = await setMyPassword({ password: passwordForm.password })
    auth.setUser(u)
    return true
  } catch (e) {
    const { code, message } = parseAuthError(e)
    if (code === 'password_format_invalid' || code === 'http_422') {
      uni.showToast({ title: '密码 6-32 位且至少含一个数字', icon: 'none' })
    } else {
      uni.showToast({ title: message || '设置密码失败', icon: 'none' })
    }
    return false
  }
}

async function handleNext() {
  if (!canAdvance.value || submitting.value) return
  submitting.value = true
  let ok = false
  try {
    const k = currentStep.value
    if (k === 'profile') ok = await submitProfileStep()
    else if (k === 'identifier') ok = await submitIdentifierStep()
    else if (k === 'password') ok = await submitPasswordStep()
  } finally {
    submitting.value = false
  }
  if (!ok) return

  advanced.value = true
  // remainingSteps 是基于 user 实时算的, 提交完一步它会自动短一格;
  // 所以下一步直接 idx=0 重新指向第一个未完成的就行 (而不是 idx+1)
  currentStepIdx.value = 0

  if (remainingSteps.value.length === 0) {
    uni.showToast({ title: '资料完善完成!', icon: 'success' })
    setTimeout(() => {
      uni.switchTab({
        url: '/pages/index/index',
        fail: () => uni.reLaunch({ url: '/pages/index/index' }),
      })
    }, 800)
  }
}

function handleSkip() {
  uni.showModal({
    title: '暂不完善?',
    content:
      '部分功能 (订单 / 兑换 / 找回密码) 可能受限. 仍可在"我的"页随时回来补充.',
    confirmText: '仍要跳过',
    cancelText: '继续完善',
    success: (res) => {
      if (res.confirm) {
        uni.switchTab({
          url: '/pages/index/index',
          fail: () => uni.reLaunch({ url: '/pages/index/index' }),
        })
      }
    },
  })
}

// 进页时的初始化:
// - 没登录 → 不该走到这页, 兜底 reLaunch login
// - 已经全完了 → 直接 reLaunch 首页 (避免老用户更新 app 后误进入)
// - 否则用 user 当前的 nickname 预填 form
onShow(() => {
  if (!loggedIn.value) {
    uni.reLaunch({ url: '/pages/auth/login' })
    return
  }
  if (remainingSteps.value.length === 0) {
    uni.switchTab({
      url: '/pages/index/index',
      fail: () => uni.reLaunch({ url: '/pages/index/index' }),
    })
    return
  }
  if (user.value?.nickname && !profileForm.nickname) {
    profileForm.nickname = user.value.nickname
  }
  if (user.value?.avatar_url && !profileForm.avatarUrl) {
    profileForm.avatarUrl = user.value.avatar_url
  }
  currentStepIdx.value = 0
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <view class="hero">
      <text class="hero-title">完善资料</text>
      <text class="hero-sub">{{ totalSteps > 0 ? `还有 ${totalSteps} 步` : '已完成' }}</text>
    </view>

    <view class="card">
      <!-- 进度指示 -->
      <view v-if="totalSteps > 0" class="progress">
        <view
          v-for="(step, i) in remainingSteps"
          :key="step"
          :class="['progress-dot', i === currentStepIdx && 'progress-dot-active']"
        >
          {{ i + 1 }}
        </view>
      </view>
      <text class="step-label">{{ stepLabel }}</text>

      <!-- Step 1: 头像 + 昵称 -->
      <view v-if="currentStep === 'profile'" class="form">
        <!-- mp-weixin 用 chooseAvatar; 其它端给个占位提示 -->
        <!-- #ifdef MP-WEIXIN -->
        <button
          class="avatar-btn"
          open-type="chooseAvatar"
          @chooseavatar="onChooseAvatar"
        >
          <image
            v-if="profileForm.avatarUrl || user?.avatar_url"
            class="avatar-img"
            :src="profileForm.avatarUrl || user?.avatar_url || ''"
            mode="aspectFill"
          />
          <view v-else class="avatar-placeholder">
            <text>+ 选择头像</text>
          </view>
        </button>
        <text class="hint hint-center">点击头像选择 (微信会自动审核)</text>
        <!-- #endif -->
        <!-- #ifndef MP-WEIXIN -->
        <view class="avatar-placeholder">
          <text>头像暂仅微信端可设置</text>
        </view>
        <!-- #endif -->

        <view class="field">
          <text class="label">昵称</text>
          <!-- type="nickname" 在 mp-weixin 会触发微信内置昵称选择面板; 其它端走普通文本输入 -->
          <input
            v-model="profileForm.nickname"
            class="input"
            type="nickname"
            maxlength="20"
            placeholder="请填写昵称 (1-20 字)"
            placeholder-class="input-placeholder"
            @blur="onNicknameInput"
          />
        </view>
      </view>

      <!-- Step 2: 手机 / 邮箱 -->
      <view v-else-if="currentStep === 'identifier'" class="form">
        <view class="segments">
          <view
            :class="['segment', identifierForm.mode === 'phone' && 'segment-active']"
            @tap="identifierForm.mode = 'phone'"
          >
            手机号
          </view>
          <view
            :class="['segment', identifierForm.mode === 'email' && 'segment-active']"
            @tap="identifierForm.mode = 'email'"
          >
            邮箱
          </view>
        </view>

        <view v-if="identifierForm.mode === 'phone'" class="field">
          <text class="label">手机号</text>
          <input
            v-model="identifierForm.phone"
            class="input"
            type="number"
            maxlength="20"
            placeholder="请输入手机号 (国内 11 位 / +区号)"
            placeholder-class="input-placeholder"
          />
          <text class="hint">手机号补绑需通过短信验证, 当前请改用邮箱方式</text>
        </view>

        <view v-else class="field">
          <text class="label">邮箱</text>
          <input
            v-model="identifierForm.email"
            class="input"
            type="text"
            maxlength="254"
            placeholder="example@domain.com"
            placeholder-class="input-placeholder"
          />
        </view>
      </view>

      <!-- Step 3: 密码 -->
      <view v-else-if="currentStep === 'password'" class="form">
        <view class="field">
          <text class="label">设置密码</text>
          <view class="row">
            <input
              v-model="passwordForm.password"
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
            v-model="passwordForm.passwordConfirm"
            class="input"
            :password="!showPassword"
            maxlength="32"
            placeholder="再次输入密码"
            placeholder-class="input-placeholder"
          />
          <text
            v-if="
              passwordForm.passwordConfirm.length > 0 && !passwordConfirmValid
            "
            class="hint hint-error"
          >
            两次密码不一致
          </text>
        </view>
        <text class="hint">
          密码用于下次登录, 也可作为微信解绑后兜底; 请妥善保管.
        </text>
      </view>

      <view
        :class="['btn-primary', !canAdvance && 'btn-disabled']"
        @tap="handleNext"
      >
        {{ submitting ? '保存中…' : remainingSteps.length === 1 ? '完成' : '下一步' }}
      </view>

      <view v-if="advanced" class="skip-link" @tap="handleSkip">
        <text>暂时跳过, 进入首页</text>
      </view>
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
  margin-bottom: 48rpx;

  .hero-title {
    font-size: 48rpx;
    font-weight: 700;
    color: var(--color-text, #e2e8f0);
    margin-bottom: 12rpx;
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
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.progress {
  display: flex;
  justify-content: center;
  gap: 16rpx;

  .progress-dot {
    width: 56rpx;
    height: 56rpx;
    border-radius: 50%;
    background: var(--color-surface-elevated, #1a2238);
    color: var(--color-text-muted, #94a3b8);
    font-size: 28rpx;
    font-weight: 600;
    text-align: center;
    line-height: 56rpx;
  }

  .progress-dot-active {
    background: linear-gradient(135deg, #4f8bff 0%, #6b9aff 100%);
    color: #fff;
  }
}

.step-label {
  text-align: center;
  font-size: 32rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  margin: 8rpx 0 16rpx;
}

.form {
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.avatar-btn {
  width: 200rpx;
  height: 200rpx;
  margin: 0 auto;
  padding: 0;
  border: none;
  background: transparent;
  border-radius: 50%;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  line-height: normal;

  &::after {
    border: none;
  }
}

.avatar-img {
  width: 100%;
  height: 100%;
  border-radius: 50%;
}

.avatar-placeholder {
  width: 200rpx;
  height: 200rpx;
  border-radius: 50%;
  background: var(--color-surface-elevated, #1a2238);
  border: 2rpx dashed var(--color-text-muted, #94a3b8);
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto;
  color: var(--color-text-muted, #94a3b8);
  font-size: 24rpx;
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

.segments {
  display: flex;
  gap: 16rpx;
  background: var(--color-surface-elevated, #1a2238);
  border-radius: 16rpx;
  padding: 8rpx;

  .segment {
    flex: 1;
    text-align: center;
    padding: 20rpx 0;
    font-size: 28rpx;
    color: var(--color-text-muted, #94a3b8);
    border-radius: 12rpx;
  }

  .segment-active {
    color: #fff;
    background: linear-gradient(135deg, #4f8bff 0%, #6b9aff 100%);
    font-weight: 600;
  }
}

.hint {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  padding: 0 4rpx;
  line-height: 1.5;

  &.hint-center {
    text-align: center;
  }

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

.skip-link {
  text-align: center;
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  margin-top: 8rpx;
  text-decoration: underline;
}
</style>
