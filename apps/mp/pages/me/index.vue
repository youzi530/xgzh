<script setup lang="ts">
/**
 * 个人中心 (FE-003 + FE-S2-004 VIP 升级 modal + FE-S3-005 VIP 卡接真).
 *
 * 模块:
 * 1. 顶部资料卡: 头像 + 昵称 + region + 邀请码 (可点击复制)
 * 2. **VIP 入口卡 (FE-S3-005 接真)**: 4 态 (trialing / active / expired / null) +
 *    剩余天数 + 试用倒计时 (每分钟刷新) + "立即升级 / 续费 / 重新订阅" 主 CTA +
 *    "支付历史 / 管理订阅" 次 CTA. 主 CTA 跳 ``/pages/vip/index`` (FE-S3-004)
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
 *
 * VIP 卡接真 (FE-S3-005) 设计:
 * - 状态走 ``auth.vipMembership`` (BE-S3-009 ``GET /vip/me``); ``onShow`` 时
 *   ``refreshMembership()`` 拉最新; 不持久化 storage (回避"显示 active 实际 expired" 不一致)
 * - 试用 / active 用户走金色卡片 + 倒计时; expired / null 走灰色卡 + 升级 CTA
 * - 倒计时 (剩余天 / 时 / 分) 每 ``COUNTDOWN_TICK_MS=60_000`` 刷一次, ``onUnload`` 清 setInterval
 * - 不直接打 ``upgrade.open()`` 走 modal, 直接跳 ``/pages/vip/index`` 让用户清楚选套餐
 *   (modal 适合"打断用户当前任务" 场景如 quota 撞墙, me 页是用户主动来的不需要二次引导)
 */

import { onShow, onUnload } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, defineAsyncComponent, onUnmounted, reactive, ref } from 'vue'

import { updateMe } from '@/api/auth'
import { bindInvite, parseInviteError } from '@/api/invite'
import type { APIError } from '@/utils/request'
// PE-S4-001 首屏 lazy-load: VIP 升级 modal 仅在用户点"立即升级" 后弹, 95% 用户
// 进个人中心不会触发. defineAsyncComponent 让它独立成 chunk, 首屏不下载.
const UpgradeVipModal = defineAsyncComponent(
  () => import('@/components/UpgradeVipModal.vue'),
)
import { useUpgradeModal } from '@/composables/upgradeModal'
import { useAuthStore } from '@/stores/auth'
import { type ThemeMode, useThemeStore } from '@/stores/theme'

const KEY_BOUND_REFERRER = 'xgzh.invite.bound_referrer'
/** 试用 / 订阅倒计时刷新间隔; 1 分钟 — 比"剩余天数"刻度细一档, 给视觉"在跑" */
const COUNTDOWN_TICK_MS = 60_000

/**
 * BUG-S7.0-002: 商务合作微信号占位符.
 *
 * 上线前替换为运营实际微信号; 一处 const 维护. 走环境变量也 OK 但 MVP 阶段
 * 单值 hardcode 最快 (用户拍板 ``placeholder`` 选项).
 */
const BD_WECHAT_ID = 'xinguzhihui-bd'

const authStore = useAuthStore()
const { user, loggedIn, vipMembership, vipMembershipLoading } = storeToRefs(authStore)
const upgrade = useUpgradeModal()

// FE-S4-004: 主题切换器 — 读 mode 给 segment 高亮, 写 mode 立即生效 + 持久化
const themeStore = useThemeStore()
const { mode: themeMode } = storeToRefs(themeStore)

interface ThemeOption {
  key: ThemeMode
  label: string
  emoji: string
}

const THEME_OPTIONS: ThemeOption[] = [
  { key: 'auto', label: '跟随系统', emoji: '🌗' },
  { key: 'dark', label: '深色', emoji: '🌙' },
  { key: 'light', label: '浅色', emoji: '☀️' },
]

/**
 * BUG-S7.0-004: 外观主题入口收纳进"设置/关于" link-list.
 *
 * 走 ``uni.showActionSheet`` 跨端原生 (H5 / 微信小程序 / App 都支持), 0 额外
 * 组件成本. itemList 顺序与 ``THEME_OPTIONS`` 严格对齐, 用户点选后 tapIndex
 * 直接索引数组取 key. 当前已选项前加 ``✓`` 让用户清楚状态.
 *
 * 与原"独立 section + segment 三块"相比, 收纳后:
 * - 我的页一屏密度下降, 协议 / 关于这类低频项更突出
 * - 主题切换是中频操作 (一周 1-2 次), 二级入口足够
 * - 与 ``openLegal`` 同位置, 用户预期"设置类"操作集中在最下
 */
function openThemePicker() {
  const cur = themeMode.value
  const itemList = THEME_OPTIONS.map(
    (t) => `${t.emoji} ${t.label}${t.key === cur ? '  ✓' : ''}`,
  )
  uni.showActionSheet({
    itemList,
    success: (res) => {
      const picked = THEME_OPTIONS[res.tapIndex]
      if (!picked || picked.key === cur) return
      themeStore.setMode(picked.key)
      uni.showToast({ title: `已切换为${picked.label}`, icon: 'success' })
    },
  })
}

/**
 * BUG-S7.0-002: 复制商务合作微信号.
 *
 * 走 ``uni.setClipboardData``, 跨端兼容 (H5 也走 fallback execCommand);
 * 复制成功 toast, 失败 toast. 主流程兼容剪贴板权限拒绝 (用户拒绝时 fail 回调).
 */
function copyBdWechat() {
  uni.setClipboardData({
    data: BD_WECHAT_ID,
    success: () => uni.showToast({ title: '微信号已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败, 请手动选中', icon: 'none' }),
  })
}

// BUG-S6.5-006: 我的自选下沉到首页 segment-tab 后, 我的页不再展示自选 entry
// 也不再需要预热 favorites store (首页 segment 自己会 loadOnce)。
// favStore 引用保留用于其它跨页可能的复用 (如 IPO 详情页 FavoriteButton 已自带 loadOnce)。

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
  // 防御性: me 页本身不应该自动弹升级 modal (gotoVip 是 user-initiated 才弹).
  // 但 modal 是模块级单例 visible, 上一页 (例如 agent 页 quota 错) 没成功 close
  // 时切回 me 页会 stale 显示. 这里 onShow 强制 close — 与 auth setSession 的
  // reset 是双保险: 即便用户没经历 setSession (例如冷启动已经登录), me 页 onShow
  // 时把 modal 复位.
  upgrade.close()
  const cached = uni.getStorageSync(KEY_BOUND_REFERRER) as string | ''
  boundReferrer.value = cached || null
  // BUG-S6.5-006: 自选下沉到首页, 不再在我的页预热; 首页 segment 切到"我的自选"
  // 时由 useFavoritesStore.ensureLoaded() 自己处理。
  // 刷新 VIP 状态; 失败也不影响页面渲染, 卡片走 fallback 文案
  void authStore.refreshMembership()
}

// ─── VIP 卡四态 ─────────────────────────────────────────────
// 倒计时滴答 (每分钟自增, 触发 ``vipDaysLabel`` / ``vipFootText`` re-compute)
const tick = ref(0)
let countdownTimer: number | null = null

function startCountdown() {
  if (countdownTimer !== null) return
  countdownTimer = setInterval(() => {
    tick.value += 1
  }, COUNTDOWN_TICK_MS) as unknown as number
}

function stopCountdown() {
  if (countdownTimer !== null) {
    clearInterval(countdownTimer)
    countdownTimer = null
  }
}

/**
 * VIP 卡视觉 + 文案分发四态:
 *
 * - **trialing**:  金色卡, "VIP 试用中" + 剩余 X 天 X 时, "立即升级"主 CTA
 * - **active**:    金色卡, "VIP 至 YYYY-MM-DD 到期" 或 "终身 VIP", "续费" 主 CTA
 * - **expired**:   灰色卡, "VIP 已过期" + "立即续费"主 CTA (高亮)
 * - **null**:      灰色卡, "免费会员" + "开通 VIP" 主 CTA (高亮)
 *
 * 视觉层级: 金色 = 已有权益 (静态展示); 灰色 + 高亮 CTA = 引导转化 (强引流)
 */
const vipStatus = computed<'trialing' | 'active' | 'expired' | 'null'>(() => {
  const m = vipMembership.value
  if (m?.has_active && m.status === 'trialing') return 'trialing'
  if (m?.has_active) return 'active'
  if (m?.status === 'expired' || m?.status === 'cancelled') return 'expired'
  return 'null'
})

const isVipGold = computed(() => vipStatus.value === 'trialing' || vipStatus.value === 'active')

/**
 * 倒计时显示串.
 *
 * 计算依赖 ``tick`` 让每分钟 reactive 重算 (Vue computed 默认按引用比较, 不显式
 * 引用 tick 不会重算). 不直接读 ``Date.now()`` 然后期待 setInterval 触发 — Vue 的
 * 响应式系统不会监听 Date.now()
 */
const vipDaysLabel = computed(() => {
  void tick.value // touch 让 setInterval 触发重算
  const m = vipMembership.value
  if (!m?.end_at) return ''
  if (m.plan === 'lifetime') return '永久有效'

  const remainMs = new Date(m.end_at).getTime() - Date.now()
  if (remainMs <= 0) return '已过期'
  const days = Math.floor(remainMs / 86_400_000)
  const hours = Math.floor((remainMs % 86_400_000) / 3_600_000)
  if (days >= 7) return `剩余 ${days} 天`
  // 倒数 7 天内显示精确"X 天 Y 时", 营造紧迫感
  if (days >= 1) return `剩余 ${days} 天 ${hours} 时`
  const mins = Math.floor((remainMs % 3_600_000) / 60_000)
  return `剩余 ${hours} 时 ${mins} 分`
})

/** 主标题 */
const vipTitle = computed(() => {
  switch (vipStatus.value) {
    case 'trialing':
      return 'VIP 试用中'
    case 'active':
      if (vipMembership.value?.plan === 'lifetime') return '终身 VIP'
      return 'VIP 已激活'
    case 'expired':
      return 'VIP 已过期'
    case 'null':
    default:
      return '免费会员'
  }
})

/** 副标题 + 倒计时 */
const vipDesc = computed(() => {
  const m = vipMembership.value
  switch (vipStatus.value) {
    case 'trialing':
      return `${vipDaysLabel.value} · 升级解锁全部权益`
    case 'active':
      if (m?.plan === 'lifetime') return '解锁全部 AI 深度功能'
      if (m?.end_at) return `有效期至 ${formatDate(m.end_at)} · ${vipDaysLabel.value}`
      return '已激活'
    case 'expired':
      return '续费即可恢复全部权益'
    case 'null':
    default:
      return '解锁 AI 深度诊断 / 历史数据 / 提醒'
  }
})

/** 主 CTA 文案 */
const vipCtaText = computed(() => {
  switch (vipStatus.value) {
    case 'trialing':
      return '立即升级'
    case 'active':
      if (vipMembership.value?.plan === 'lifetime') return '查看权益'
      return '续费'
    case 'expired':
      return '立即续费'
    case 'null':
    default:
      return '开通 VIP'
  }
})

/** 主 CTA 是否走"高亮金色"突出 (引导用户转化的态: expired / null / trialing) */
const vipCtaHighlight = computed(
  () => vipStatus.value === 'trialing' || vipStatus.value === 'expired' || vipStatus.value === 'null',
)

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

function gotoOrders() {
  uni.navigateTo({ url: '/pages/me/orders' })
}

function showManageSubscription() {
  uni.showModal({
    title: '管理订阅',
    content:
      '本应用当前为单次支付订阅 (非自动续费), 到期后自动失效, 不会自动扣款.\n\n如需取消已生效订阅, 请联系客服: contact@example.com',
    showCancel: false,
    confirmText: '我知道了',
  })
}

function gotoFeedback() {
  uni.navigateTo({ url: '/pages/me/feedback' })
}

// BUG-S6.5-004b: 券商对比从首页右上角图标按钮迁到这里 — 用户视券商为
// "我的工具" 比"首页公开入口"更合适, 也清空首页 hero 视觉负担。
function gotoBrokers() {
  uni.navigateTo({ url: '/pages/broker/index' })
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

/**
 * BUG-S6.8-002: 修改昵称 — 用 ``uni.showModal`` 的 ``editable`` 模式弹输入框,
 * 跨三端 (H5 / 微信小程序 / App) 都支持, 不需要自实现 modal 组件.
 *
 * 错误码差异化 toast (与 PATCH /me 后端契约对齐):
 * - ``nickname_empty`` / ``nickname_too_long`` → 直接显示 message
 * - 422 (Pydantic min_length / max_length) → 兜底 "昵称需 1-20 字"
 * - 其它 → "保存失败, 稍后重试"
 */
async function editNickname() {
  if (!user.value) return
  const current = user.value.nickname || ''
  const ret = await new Promise<{ confirm: boolean; content: string }>((resolve) => {
    uni.showModal({
      title: '修改昵称',
      placeholderText: '1-20 字, 中英文均可',
      content: current,
      editable: true,
      success: (r) => resolve({ confirm: !!r.confirm, content: r.content || '' }),
      fail: () => resolve({ confirm: false, content: '' }),
    })
  })
  if (!ret.confirm) return
  const next = ret.content.trim()
  if (!next) {
    uni.showToast({ title: '昵称不能为空', icon: 'none' })
    return
  }
  if (next === current) return // 没改 — 静默退出
  if (next.length > 20) {
    uni.showToast({ title: '昵称最长 20 字', icon: 'none' })
    return
  }
  try {
    const updated = await updateMe({ nickname: next })
    authStore.setUser(updated)
    uni.showToast({ title: '昵称已更新', icon: 'success' })
  } catch (e) {
    const err = e as APIError
    // 后端 ``HTTPException(detail={"code": ..., "message": ...})`` → request.ts
    // 把整段挂到 ``err.detail``; Pydantic 422 走 ``detail = [{loc, msg}, ...]``,
    // 这里只取 dict 形式的 ``code`` 做差异化, 其它走兜底 message。
    let code: string | undefined
    if (err.detail && typeof err.detail === 'object' && !Array.isArray(err.detail)) {
      const d = err.detail as { code?: string }
      code = d.code
    }
    let msg = '保存失败, 稍后重试'
    if (code === 'nickname_empty') msg = '昵称不能为空'
    else if (code === 'nickname_too_long') msg = '昵称最长 20 字'
    else if (err.statusCode === 422) msg = '昵称需 1-20 字'
    uni.showToast({ title: msg, icon: 'none' })
  }
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

/**
 * VIP 卡主 CTA: 直接跳 ``/pages/vip/index`` (FE-S3-004).
 *
 * **不走 modal 的原因**: me 页是用户主动来的, 不需要二次引导 modal "解释 VIP 是什么"
 * (modal 适合 quota 撞墙这种"打断"场景, 给用户"我撞了什么 → 怎么解决"的归因).
 * 用户在 me 页点 VIP 卡 = 已经"打算买", 直接进选套餐页转化路径最短.
 *
 * VIP 升级页 (FE-S3-004) 内部有套餐对比 / 权益矩阵 / 真实下单 + uni.requestPayment;
 * me 页负责"展示当前状态 + 引流", 不重复造轮子.
 */
function gotoVip() {
  uni.navigateTo({ url: '/pages/vip/index' })
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
  // 启动倒计时滴答; trialing 用户最关心剩余时间, 每分钟自动刷一次让"剩 X 时 Y 分"持续走动
  startCountdown()
})

onUnload(() => {
  stopCountdown()
})

// onUnmounted 兜底 (uni-app 不一定每次都触发 onUnload, 例如 H5 路由切走时)
onUnmounted(() => {
  stopCountdown()
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
        <view class="nickname-row" @tap="editNickname">
          <text class="profile-nickname">{{ displayNickname }}</text>
          <text class="nickname-edit">编辑</text>
        </view>
        <text class="profile-region">{{ displayRegion }}</text>
        <view class="invite-row" @tap="copyInviteCode">
          <text class="invite-label">我的邀请码</text>
          <text class="invite-code">{{ user.invite_code }}</text>
          <text class="invite-copy">复制</text>
        </view>
      </view>
    </view>

    <view :class="['vip-card', isVipGold ? 'vip-card-gold' : 'vip-card-gray']">
      <view class="vip-card-main" @tap="gotoVip">
        <view class="vip-left">
          <view class="vip-title-row">
            <text v-if="isVipGold" class="vip-crown">👑</text>
            <text class="vip-tag">{{ vipMembershipLoading && !vipMembership ? '加载中…' : vipTitle }}</text>
          </view>
          <text class="vip-desc">{{ vipDesc }}</text>
        </view>
        <view :class="['vip-cta', vipCtaHighlight && 'vip-cta-highlight']">
          <text class="vip-cta-text">{{ vipCtaText }}</text>
        </view>
      </view>
      <!-- 下方双入口: 支付历史 + 管理订阅; 仅在已有订阅记录时显 -->
      <view v-if="vipMembership?.membership_id" class="vip-card-foot">
        <view class="vip-foot-item" hover-class="vip-foot-item-hover" :hover-stay-time="80" @tap="gotoOrders">
          <text class="vip-foot-text">支付历史</text>
          <text class="vip-foot-arrow">›</text>
        </view>
        <view class="vip-foot-divider" />
        <view
          class="vip-foot-item"
          hover-class="vip-foot-item-hover"
          :hover-stay-time="80"
          @tap="showManageSubscription"
        >
          <text class="vip-foot-text">管理订阅</text>
          <text class="vip-foot-arrow">›</text>
        </view>
      </view>
    </view>

    <view class="entry-list">
      <!--
        BUG-S6.5-006: 我的自选已下沉到首页 segment-tab; 这里完全移除入口
        避免一处功能两个入口造成认知负担。favorites.vue 全屏页保留 (从
        首页 segment 内卡片仍可 navigateTo, 高级操作如设置提醒还在那里)。
      -->

      <!--
        BUG-S6.5-004b: 券商从首页右上角迁来 (用户相关工具集中放"我的")
        BUG-S7.0-003: 改名"券商对比" → "券商开户" — 用户的真实意图是开户走优惠
        通道, 而非纯横向对比表; 描述也调整为"开户优惠 / 佣金 / 评分".
        navbar 标题在 pages.json 同步改, vip 套餐对比表也改, 三处一致.
      -->
      <view class="entry-item" @tap="gotoBrokers">
        <view class="entry-left">
          <text class="entry-icon entry-icon-broker">🏦</text>
          <view class="entry-text">
            <text class="entry-title">券商开户</text>
            <text class="entry-desc">港 A 主流券商开户优惠 / 佣金 / 评分</text>
          </view>
        </view>
        <view class="entry-right">
          <text class="entry-arrow">›</text>
        </view>
      </view>
      <!-- FE-S5-002: 反馈入口 (对接 BE-S5-004 POST /api/v1/feedback) -->
      <view class="entry-item entry-item-bordered" @tap="gotoFeedback">
        <view class="entry-left">
          <text class="entry-icon entry-icon-feedback">💬</text>
          <view class="entry-text">
            <text class="entry-title">反馈与建议</text>
            <text class="entry-desc">问题 / 功能建议 / 内容质量</text>
          </view>
        </view>
        <view class="entry-right">
          <text class="entry-arrow">›</text>
        </view>
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

    <!--
      BUG-S7.0-002: 商务合作 — 留运营对接微信号给商务伙伴 / 大V / 投放方;
      点击复制即可, 不再加二级页. 与"邀请码 / 关于"低频但必要的入口同位置.
    -->
    <view class="section">
      <view class="section-header">
        <text class="section-title">商务合作</text>
        <text class="section-subtitle">广告投放 / 大V 合作 / 内容互推</text>
      </view>
      <view class="bd-row" hover-class="bd-row-hover" :hover-stay-time="80" @tap="copyBdWechat">
        <view class="bd-info">
          <text class="bd-label">微信号</text>
          <text class="bd-code">{{ BD_WECHAT_ID }}</text>
        </view>
        <text class="bd-copy">复制</text>
      </view>
    </view>

    <!--
      BUG-S7.0-004: 外观主题从一级 section 收纳到"设置/关于" link-list 内,
      点击走 uni.showActionSheet 弹原生选项. 我的页一屏密度下降, 协议类
      低频项更突出; 主题切换走二级入口符合"中频操作"定位.
    -->
    <view class="section">
      <view class="section-header">
        <text class="section-title">设置 / 关于</text>
      </view>
      <view class="link-list">
        <view class="link-item" @tap="openThemePicker">
          <text class="link-text">外观主题</text>
          <text class="link-arrow">›</text>
        </view>
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

    <!-- FE-S2-004: VIP 升级 modal; 状态走 useUpgradeModal() 单例, 与 agent 页共享 -->
    <UpgradeVipModal />
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
.nickname-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
}
.profile-nickname {
  font-size: 36rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
/* BUG-S6.8-002: 编辑入口 — 弱视觉的 chip 样式, 不抢主信息 */
.nickname-edit {
  font-size: 22rpx;
  color: var(--color-accent, #4f8bff);
  padding: 4rpx 14rpx;
  border-radius: 999rpx;
  background: rgba(79, 139, 255, 0.12);
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
  border-radius: 24rpx;
  padding: 0;
  overflow: hidden;
  border: 1rpx solid;
}
.vip-card-gold {
  background: linear-gradient(135deg, rgba(246, 196, 83, 0.2), rgba(79, 139, 255, 0.1));
  border-color: rgba(246, 196, 83, 0.45);
  box-shadow: 0 4rpx 20rpx rgba(246, 196, 83, 0.08);
}
.vip-card-gray {
  background: var(--color-surface, #131a2c);
  border-color: var(--color-border, rgba(255, 255, 255, 0.08));
}

.vip-card-main {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24rpx;
  padding: 32rpx;
}

.vip-left {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.vip-title-row {
  display: flex;
  align-items: center;
  gap: 12rpx;
}
.vip-crown {
  font-size: 32rpx;
  line-height: 1;
}
.vip-tag {
  font-size: 30rpx;
  font-weight: 700;
}
.vip-card-gold .vip-tag {
  color: #f6c453;
}
.vip-card-gray .vip-tag {
  color: var(--color-text, #e2e8f0);
}
.vip-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
}
.vip-cta {
  flex-shrink: 0;
  padding: 16rpx 32rpx;
  border-radius: 999rpx;
  background: rgba(246, 196, 83, 0.16);
  border: 1rpx solid rgba(246, 196, 83, 0.32);
}
.vip-cta-highlight {
  background: linear-gradient(135deg, #f6c453, #d97706);
  border-color: transparent;
  box-shadow: inset 0 1rpx 0 rgba(255, 255, 255, 0.32);
}
.vip-cta-text {
  font-size: 26rpx;
  font-weight: 700;
  color: #f6c453;
}
.vip-cta-highlight .vip-cta-text {
  color: #1a1305;
}

.vip-card-foot {
  display: flex;
  flex-direction: row;
  border-top: 1rpx solid rgba(246, 196, 83, 0.18);
}
.vip-card-gray .vip-card-foot {
  border-top-color: rgba(255, 255, 255, 0.06);
}
.vip-foot-item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8rpx;
  padding: 22rpx 0;
}
.vip-foot-item-hover {
  background: rgba(255, 255, 255, 0.04);
}
.vip-foot-text {
  font-size: 24rpx;
  color: var(--color-text, #e2e8f0);
  opacity: 0.9;
}
.vip-foot-arrow {
  font-size: 26rpx;
  color: var(--color-text-muted, #94a3b8);
  opacity: 0.7;
}
.vip-foot-divider {
  width: 1rpx;
  margin: 12rpx 0;
  background: rgba(255, 255, 255, 0.08);
}

.entry-list {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 8rpx 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.entry-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24rpx 0;
}
.entry-item-bordered {
  border-top: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.04));
}
.entry-icon-feedback {
  background: rgba(79, 139, 255, 0.15);
  border-color: rgba(79, 139, 255, 0.4);
  color: var(--color-primary, #4f8bff);
}
.entry-icon-broker {
  background: rgba(34, 197, 94, 0.15);
  border-color: rgba(34, 197, 94, 0.35);
  color: #22c55e;
}
.entry-left {
  flex: 1;
  display: flex;
  align-items: center;
  gap: 20rpx;
  min-width: 0;
}
.entry-icon {
  width: 64rpx;
  height: 64rpx;
  flex-shrink: 0;
  border-radius: 16rpx;
  background: rgba(246, 196, 83, 0.15);
  border: 1rpx solid rgba(246, 196, 83, 0.35);
  color: #f6c453;
  font-size: 32rpx;
  text-align: center;
  line-height: 64rpx;
}
.entry-text {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  min-width: 0;
}
.entry-title {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.entry-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.entry-right {
  display: flex;
  align-items: center;
  gap: 12rpx;
  flex-shrink: 0;
}
.entry-badge {
  min-width: 40rpx;
  height: 40rpx;
  padding: 0 12rpx;
  line-height: 40rpx;
  text-align: center;
  background: rgba(79, 139, 255, 0.18);
  border: 1rpx solid rgba(79, 139, 255, 0.4);
  border-radius: 999rpx;
  color: var(--color-primary, #4f8bff);
  font-size: 22rpx;
  font-weight: 700;
}
.entry-arrow {
  font-size: 36rpx;
  color: var(--color-text-muted, #94a3b8);
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

/*
  BUG-S7.0-004: ``.theme-seg*`` segment 样式已废弃 — "外观主题"收纳进
  "设置/关于" link-list, 走 uni.showActionSheet 选项, 不再有 inline segment.
  样式不留, 避免冗余 CSS.
*/

/* BUG-S7.0-002: 商务合作模块样式 — 与 invite-row (邀请码) 同款"可点 chip 风格" */
.bd-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16rpx;
  padding: 16rpx 20rpx;
  background: rgba(34, 197, 94, 0.06);
  border: 1rpx solid rgba(34, 197, 94, 0.2);
  border-radius: 12rpx;
}
.bd-row-hover {
  background: rgba(34, 197, 94, 0.12);
}
.bd-info {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  min-width: 0;
}
.bd-label {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.bd-code {
  font-size: 28rpx;
  font-weight: 700;
  color: #22c55e;
  letter-spacing: 1rpx;
}
.bd-copy {
  flex-shrink: 0;
  font-size: 22rpx;
  color: #22c55e;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(34, 197, 94, 0.12);
  border: 1rpx solid rgba(34, 197, 94, 0.3);
}
</style>
