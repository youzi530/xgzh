<script setup lang="ts">
/**
 * 反馈与建议表单页 (FE-S5-002 / 对接 BE-S5-004 ``POST /api/v1/feedback``).
 *
 * 路由: ``/pages/me/feedback``  (从个人中心入口卡片跳转)
 *
 * 模块:
 * 1. 反馈类型 segment (4 选 1: bug / feature / content / other)
 * 2. 详细描述 textarea (1 ~ 2000 字, 实时计数)
 * 3. 联系方式 input (可选, ≤ 64 字; phone / email / 微信号都行)
 * 4. 提交按钮 (loading / disabled / 二次防重)
 * 5. 钉钉群二维码占位 ("加群快速反馈" — 静态占位, 上线前替换真二维码)
 *
 * 设计取舍:
 *
 * - **不在前端做敏感词过滤**: 反馈本来就该让用户能投诉; BE-S5-001 红线词在 LLM
 *   流式产出阶段拦, 反馈写入只在 admin 视角看到时打 logger.warning, 不阻塞用户提交
 *
 * - **submit 后 1s 回上一页**: 给 toast 渲染时间; 不放"再写一条"按钮 — 大多数用户
 *   反馈一次就走, 留页只增加误提多发风险
 *
 * - **失败不清空表单**: 用户辛苦写的 2000 字别因网络抖动 / 限流被清掉. 提交按钮
 *   loading 结束即可让用户 retry
 *
 * - **暗黑模式**: 全部走 ``var(--color-*)`` token, 与 me/index.vue 同款 fallback
 *   (FE-S4-004 主题切换适配)
 *
 * - **不做 contact 格式校验**: 用户可能留 ``18888888888`` / ``a@b.com`` /
 *   ``wx_id_2024`` 任何格式, 强校验反而劝退. 后端 ≤ 64 字, 前端只 maxlength 限
 */

import { onLoad } from '@dcloudio/uni-app'
import { computed, reactive, ref } from 'vue'

import { type FeedbackCategory, detectPlatform, parseFeedbackError, submitFeedback } from '@/api/feedback'

const APP_VERSION = '0.1.0'  // 与 manifest 版本同步; FE-S5-001 提审时统一改

interface CategoryOption {
  key: FeedbackCategory
  label: string
  emoji: string
  desc: string
}

const CATEGORIES: CategoryOption[] = [
  { key: 'bug', label: '问题反馈', emoji: '🐛', desc: '功能异常 / 数据错乱 / 闪退' },
  { key: 'feature', label: '功能建议', emoji: '💡', desc: '希望增加 / 优化的功能' },
  { key: 'content', label: '内容质量', emoji: '📝', desc: '文章错误 / AI 答非所问' },
  { key: 'other', label: '其它反馈', emoji: '💬', desc: '体验吐槽 / 商务合作' },
]

const form = reactive({
  category: 'bug' as FeedbackCategory,
  content: '',
  contact: '',
})
const submitting = ref(false)

const contentLength = computed(() => form.content.length)
const contentTooShort = computed(() => contentLength.value === 0)
const contentTooLong = computed(() => contentLength.value > 2000)

/** 提交按钮 disabled: 内容为空 / 超长 / 正在提交 */
const submitDisabled = computed(
  () => contentTooShort.value || contentTooLong.value || submitting.value,
)

const submitText = computed(() => {
  if (submitting.value) return '提交中...'
  return '提交反馈'
})

function selectCategory(key: FeedbackCategory) {
  form.category = key
}

async function handleSubmit() {
  if (submitDisabled.value) return

  submitting.value = true
  try {
    await submitFeedback({
      category: form.category,
      content: form.content.trim(),
      contact: form.contact.trim() || undefined,
      app_version: APP_VERSION,
      platform: detectPlatform(),
    })
    uni.showToast({
      title: '提交成功, 感谢你的反馈',
      icon: 'success',
      duration: 1500,
    })
    // 给 toast 渲染时间, 1s 后回上一页 (用户主流路径: 提交 → 离开页面)
    setTimeout(() => {
      uni.navigateBack({ delta: 1 }).catch(() => {
        // 兜底: 没有上一页时跳个人中心 tab (例如直接 H5 输入 URL 进来)
        // FE-S6-001: tabBar 启用后, me 是 tab 页, switchTab 优于 reLaunch (保 state)
        uni.switchTab({
          url: '/pages/me/index',
          fail: () => uni.reLaunch({ url: '/pages/me/index' }),
        })
      })
    }, 1000)
  } catch (err) {
    const { code, message } = parseFeedbackError(err)
    let title = message || '提交失败'
    // 撞限流时给精准文案; 其他错误码透传 message (BE Pydantic 校验 / 500 等)
    if (code === 'too_many_requests') {
      title = '提交过于频繁, 请稍后再试'
    }
    uni.showToast({ title, icon: 'none', duration: 2000 })
    // **不**清空表单 — 让用户辛苦写的内容保留, retry 即可
  } finally {
    submitting.value = false
  }
}

function showQrcodeHint() {
  uni.showModal({
    title: '加入反馈群',
    content: '钉钉群二维码上线前补充, 当前可通过本表单或邮件 contact@example.com 反馈',
    showCancel: false,
    confirmText: '我知道了',
  })
}

onLoad(() => {
  // 仅做日志埋点占位 — UTM 审计走 FE-S5-004
})
</script>

<template>
  <view class="page">
    <!-- 顶部说明区 -->
    <view class="hero">
      <text class="hero-title">反馈与建议</text>
      <text class="hero-desc">
        遇到问题 / 有想要的功能 / 觉得哪里不顺手, 都可以告诉我们. 我们会在 3 个工作日内通过你留下的联系方式回复.
      </text>
    </view>

    <!-- 反馈类型 -->
    <view class="section">
      <view class="section-header">
        <text class="section-title">反馈类型</text>
        <text class="section-required">必选</text>
      </view>
      <view class="cat-grid">
        <view
          v-for="c in CATEGORIES"
          :key="c.key"
          :class="['cat-item', form.category === c.key && 'cat-item-active']"
          hover-class="cat-item-hover"
          :hover-stay-time="80"
          @tap="selectCategory(c.key)"
        >
          <text class="cat-emoji">{{ c.emoji }}</text>
          <text class="cat-label">{{ c.label }}</text>
          <text class="cat-desc">{{ c.desc }}</text>
        </view>
      </view>
    </view>

    <!-- 详细描述 -->
    <view class="section">
      <view class="section-header">
        <text class="section-title">详细描述</text>
        <text :class="['section-counter', contentTooLong && 'section-counter-error']">
          {{ contentLength }} / 2000
        </text>
      </view>
      <textarea
        v-model="form.content"
        class="content-input"
        :class="contentTooLong && 'content-input-error'"
        maxlength="2200"
        placeholder="尽可能描述清楚: 在哪个页面 / 做了什么操作 / 期待结果 / 实际结果. 截图截屏可以发到客服邮箱."
        placeholder-class="content-placeholder"
        auto-height
      />
      <text v-if="contentTooLong" class="hint-error">
        超过 2000 字了, 请精简一下
      </text>
    </view>

    <!-- 联系方式 -->
    <view class="section">
      <view class="section-header">
        <text class="section-title">联系方式</text>
        <text class="section-optional">可选</text>
      </view>
      <input
        v-model="form.contact"
        class="contact-input"
        maxlength="64"
        placeholder="手机号 / 邮箱 / 微信号 — 留下后我们才能回复你"
        placeholder-class="content-placeholder"
      />
      <text class="hint-text">
        我们仅用于回复本次反馈, 不会做任何其它用途.
      </text>
    </view>

    <!-- 钉钉群占位 -->
    <view class="qr-card" hover-class="qr-card-hover" :hover-stay-time="80" @tap="showQrcodeHint">
      <view class="qr-left">
        <text class="qr-icon">📣</text>
        <view class="qr-text">
          <text class="qr-title">加入反馈群</text>
          <text class="qr-desc">紧急问题快速响应 (钉钉群)</text>
        </view>
      </view>
      <text class="qr-arrow">›</text>
    </view>

    <!-- 提交按钮 -->
    <button
      class="submit-btn"
      :class="{ 'submit-btn-disabled': submitDisabled }"
      :disabled="submitDisabled"
      @tap="handleSubmit"
    >
      {{ submitText }}
    </button>

    <!-- 法律小字 -->
    <text class="legal-text">
      提交即视为同意《用户协议》, 本平台为信息聚合工具, 不构成投资建议
    </text>
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

.hero {
  display: flex;
  flex-direction: column;
  gap: 12rpx;
  padding: 8rpx 4rpx;
}
.hero-title {
  font-size: 36rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.hero-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
}

.section {
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  padding: 28rpx 24rpx;
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  display: flex;
  flex-direction: column;
  gap: 20rpx;
}
.section-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.section-title {
  font-size: 28rpx;
  font-weight: 700;
  color: var(--color-text, #e2e8f0);
}
.section-required {
  font-size: 22rpx;
  color: #ef4444;
}
.section-optional {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.section-counter {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  font-variant-numeric: tabular-nums;
}
.section-counter-error {
  color: #ef4444;
}

.cat-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16rpx;
}
.cat-item {
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  padding: 24rpx 20rpx;
  border-radius: 16rpx;
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.cat-item-active {
  background: rgba(79, 139, 255, 0.12);
  border-color: var(--color-primary, #4f8bff);
  box-shadow: 0 0 0 2rpx rgba(79, 139, 255, 0.18);
}
.cat-item-hover {
  background: rgba(255, 255, 255, 0.08);
}
.cat-emoji {
  font-size: 36rpx;
  line-height: 1;
}
.cat-label {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
  margin-top: 4rpx;
}
.cat-item-active .cat-label {
  color: var(--color-primary, #4f8bff);
}
.cat-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.4;
}

.content-input {
  width: 100%;
  min-height: 240rpx;
  padding: 20rpx 24rpx;
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 16rpx;
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
  line-height: 1.6;
  box-sizing: border-box;
}
.content-input-error {
  border-color: #ef4444;
}
.content-placeholder {
  color: var(--color-text-muted, #94a3b8);
  font-size: 26rpx;
  line-height: 1.6;
}
.hint-error {
  display: block;
  margin-top: 8rpx;
  font-size: 22rpx;
  color: #ef4444;
}
.hint-text {
  display: block;
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.5;
}

.contact-input {
  width: 100%;
  height: 80rpx;
  padding: 0 24rpx;
  background: rgba(255, 255, 255, 0.04);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 16rpx;
  font-size: 28rpx;
  color: var(--color-text, #e2e8f0);
  box-sizing: border-box;
}

.qr-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 24rpx;
  border-radius: 24rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
}
.qr-card-hover {
  background: rgba(255, 255, 255, 0.04);
}
.qr-left {
  display: flex;
  align-items: center;
  gap: 20rpx;
  flex: 1;
  min-width: 0;
}
.qr-icon {
  width: 64rpx;
  height: 64rpx;
  flex-shrink: 0;
  border-radius: 16rpx;
  background: rgba(246, 196, 83, 0.15);
  border: 1rpx solid rgba(246, 196, 83, 0.35);
  font-size: 32rpx;
  text-align: center;
  line-height: 64rpx;
}
.qr-text {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
  min-width: 0;
}
.qr-title {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}
.qr-desc {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.qr-arrow {
  font-size: 36rpx;
  color: var(--color-text-muted, #94a3b8);
  flex-shrink: 0;
}

.submit-btn {
  margin-top: 8rpx;
  height: 96rpx;
  line-height: 96rpx;
  border-radius: 16rpx;
  background: var(--color-primary, #4f8bff);
  color: #fff;
  font-size: 30rpx;
  font-weight: 600;
  border: none;
}
.submit-btn-disabled {
  opacity: 0.5;
}

.legal-text {
  display: block;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  margin-top: 8rpx;
  line-height: 1.6;
}
</style>
