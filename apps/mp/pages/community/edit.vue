<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 社区发帖页 (FE-S6-006 接 BE-S6-006/008/009).
 *
 * 路由: /pages/community/edit
 *
 * 字段:
 * - 内容 textarea (必填, 1-500 字)
 * - 类别 segment (general / ipo_discuss / experience)
 * - 关联 IPO 代码 (optional, 16 字以内)
 *
 * 设计要点:
 *
 * - **必登录**: 进页前 ``readAccessTokenSync`` 拦; 没登录直接跳 ``/pages/auth/login``
 * - **客户端简化版违禁词提示**: 输入时实时检 ``CLIENT_FORBIDDEN_KW``; 命中时
 *   提示用户(不阻止提交); 真正决策走 BE-S6-008 v3 审核 (BE 是真理之源)
 * - **失败保留输入**: 内容违规 / 限流 / 网络错误都不清空, 让用户改 / 重试
 * - **submit 后 toast**:
 *   - status='published' → "已发布"
 *   - status='pending' → "待审核, 通过后将公开显示"
 *   - status='rejected' → "内容违规: ${rejection_reason}", 不跳页
 */

import { computed, reactive, ref } from 'vue'

import {
  type PostCategory,
  type PostCreateRequest,
  createPost,
  parseCommunityError,
} from '@/api/community'
import { readAccessTokenSync } from '@/stores/auth'

interface CategoryOption {
  key: PostCategory
  label: string
  desc: string
}

const CATEGORIES: CategoryOption[] = [
  { key: 'general', label: '综合', desc: '随便聊聊' },
  { key: 'ipo_discuss', label: '新股讨论', desc: '某只新股的看法' },
  { key: 'experience', label: '经验分享', desc: '打新经验 / 中签经历' },
]

// 客户端简化版 Tier 1 提示词 (15 个核心词); BE 是真理之源
const CLIENT_FORBIDDEN_KW = [
  '必涨',
  '必赚',
  '包赚',
  '稳赚',
  '保本',
  '无风险',
  '内幕',
  '建议买入',
  '强烈推荐',
  '闭眼买',
  '加微信',
  '加群',
  'vx',
  '私聊我',
  '扫码',
]

const form = reactive<{
  content: string
  category: PostCategory
  related_ipo_code: string
}>({
  content: '',
  category: 'general',
  related_ipo_code: '',
})

const submitting = ref(false)

// auth 守卫
if (readAccessTokenSync() === null) {
  uni.redirectTo({ url: '/pages/auth/login' })
}

const contentLength = computed(() => form.content.length)
const contentTooShort = computed(() => contentLength.value === 0)
const contentTooLong = computed(() => contentLength.value > 500)

const clientHits = computed(() => {
  const hits: string[] = []
  for (const k of CLIENT_FORBIDDEN_KW) {
    if (form.content.toLowerCase().includes(k.toLowerCase())) hits.push(k)
  }
  return hits
})

const submitDisabled = computed(
  () => contentTooShort.value || contentTooLong.value || submitting.value,
)

function selectCategory(key: PostCategory) {
  form.category = key
}

async function handleSubmit() {
  if (submitDisabled.value) return
  if (clientHits.value.length > 0) {
    const hits = clientHits.value.join(' / ')
    const ok = await new Promise<boolean>((resolve) => {
      uni.showModal({
        title: '内容可能违规',
        content: `检测到敏感词: ${hits}\n\n继续提交可能被审核拒绝, 仍要提交吗?`,
        confirmText: '仍要提交',
        cancelText: '修改',
        success: (res) => resolve(res.confirm),
      })
    })
    if (!ok) return
  }

  submitting.value = true
  try {
    const req: PostCreateRequest = {
      content: form.content.trim(),
      category: form.category,
    }
    if (form.related_ipo_code.trim()) {
      req.related_ipo_code = form.related_ipo_code.trim().toUpperCase()
    }
    const post = await createPost(req)
    if (post.status === 'published') {
      uni.showToast({ title: '已发布', icon: 'success', duration: 1200 })
      setTimeout(() => uni.navigateBack({ fail: () => {} }), 1000)
    } else if (post.status === 'pending') {
      uni.showModal({
        title: '提交成功',
        content: '内容正在审核中, 通过后将自动展示在社区',
        showCancel: false,
        success: () => uni.navigateBack({ fail: () => {} }),
      })
    } else if (post.status === 'rejected') {
      const reason = post.rejection_reason
        ? rejectionLabel(post.rejection_reason)
        : '请检查内容'
      uni.showModal({
        title: '内容违规',
        content: `${reason}, 已自动拒绝.\n\n请修改后再发`,
        showCancel: false,
      })
    }
  } catch (err) {
    const e = parseCommunityError(err)
    let title = e.message
    if (e.code === 'too_many_requests') title = '发帖过于频繁, 请稍后再试'
    if (e.code === 'new_user_readonly') title = '新用户 7 天内不能发帖'
    uni.showModal({
      title: '提交失败',
      content: title,
      showCancel: false,
    })
  } finally {
    submitting.value = false
  }
}

function rejectionLabel(r: string): string {
  if (r === 'content_violation') return '违反社区规则 (含承诺收益 / 推荐买入等)'
  if (r === 'spam') return '疑似私域引流 (微信 / QQ / 群号 / 二维码)'
  if (r === 'privacy_leak') return '隐私泄露 (证件号 / 手机号 / 银行卡)'
  return '内容违规'
}
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- 内容 -->
    <view class="section">
      <view class="section-head">
        <text class="section-title">内容</text>
        <text class="section-required">必填</text>
      </view>
      <textarea
        v-model="form.content"
        class="content-input"
        placeholder="说说你的看法、经验或问题..."
        maxlength="500"
        placeholder-class="placeholder"
      />
      <view class="content-foot">
        <view class="hits-row">
          <text v-if="clientHits.length > 0" class="hint-warn">
            ⚠️ 含敏感词: {{ clientHits.join(' / ') }}
          </text>
          <text v-else class="hint-ok">✓</text>
        </view>
        <text class="counter" :class="{ 'counter-warn': contentTooLong }">
          {{ contentLength }} / 500
        </text>
      </view>
    </view>

    <!-- 类别 -->
    <view class="section">
      <view class="section-head">
        <text class="section-title">类别</text>
      </view>
      <view class="cat-grid">
        <view
          v-for="c in CATEGORIES"
          :key="c.key"
          class="cat-card"
          :class="{ 'cat-card-active': form.category === c.key }"
          hover-class="cat-card-hover"
          :hover-stay-time="80"
          @tap="selectCategory(c.key)"
        >
          <text class="cat-card-label">{{ c.label }}</text>
          <text class="cat-card-desc">{{ c.desc }}</text>
        </view>
      </view>
    </view>

    <!-- 关联 IPO -->
    <view class="section">
      <view class="section-head">
        <text class="section-title">关联 IPO 代码</text>
        <text class="section-optional">可选</text>
      </view>
      <input
        v-model="form.related_ipo_code"
        class="text-input"
        placeholder="例: 00700 / 688123"
        maxlength="16"
        placeholder-class="placeholder"
      />
    </view>

    <!-- 社区规则提示 -->
    <view class="rules">
      <text class="rules-title">⚖️ 社区规则</text>
      <text class="rules-item">• 不得发布"必涨""稳赚"等收益承诺</text>
      <text class="rules-item">• 不得发布个人 / 他人证件号 / 手机号</text>
      <text class="rules-item">• 不得发布微信 / QQ / 群号等私域引流</text>
      <text class="rules-item">• 违规内容将被自动拒绝, 严重者封号</text>
    </view>

    <view class="bottom-spacer" />

    <!-- 底部 CTA -->
    <view class="cta-bar">
      <view
        class="cta-btn"
        :class="{ 'cta-btn-disabled': submitDisabled }"
        hover-class="cta-btn-hover"
        :hover-stay-time="80"
        @tap="handleSubmit"
      >
        <text class="cta-btn-text">{{ submitting ? '提交中...' : '发布' }}</text>
      </view>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 24rpx 32rpx 0;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
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
.section-head {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: baseline;
}
.section-title {
  font-size: 26rpx;
  font-weight: 700;
}
.section-required {
  font-size: 22rpx;
  color: #ef4444;
}
.section-optional {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

.content-input {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 20rpx 24rpx;
  font-size: 28rpx;
  line-height: 1.6;
  color: var(--color-text, #e2e8f0);
  width: 100%;
  min-height: 240rpx;
  box-sizing: border-box;
}
.content-foot {
  display: flex;
  flex-direction: row;
  justify-content: space-between;
  align-items: center;
}
.hits-row {
  flex: 1;
}
.hint-warn {
  font-size: 22rpx;
  color: #f6c453;
}
.hint-ok {
  font-size: 22rpx;
  color: #34d399;
}
.counter {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.counter-warn {
  color: #ef4444;
  font-weight: 700;
}

.text-input {
  background: rgba(0, 0, 0, 0.2);
  border: 1rpx solid rgba(255, 255, 255, 0.08);
  border-radius: 12rpx;
  padding: 18rpx 20rpx;
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
.placeholder {
  color: rgba(148, 163, 184, 0.4);
}

.cat-grid {
  display: flex;
  flex-direction: row;
  gap: 12rpx;
}
.cat-card {
  flex: 1;
  padding: 20rpx 16rpx;
  background: rgba(0, 0, 0, 0.15);
  border: 1rpx solid rgba(255, 255, 255, 0.06);
  border-radius: 16rpx;
  display: flex;
  flex-direction: column;
  gap: 6rpx;
  align-items: center;
}
.cat-card-active {
  background: rgba(79, 139, 255, 0.12);
  border-color: rgba(79, 139, 255, 0.5);
}
.cat-card-hover {
  background: rgba(255, 255, 255, 0.04);
}
.cat-card-label {
  font-size: 26rpx;
  font-weight: 600;
}
.cat-card-desc {
  font-size: 20rpx;
  color: var(--color-text-muted, #94a3b8);
  text-align: center;
}

.rules {
  background: rgba(246, 196, 83, 0.06);
  border: 1rpx solid rgba(246, 196, 83, 0.3);
  border-radius: 16rpx;
  padding: 20rpx 24rpx;
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.rules-title {
  font-size: 24rpx;
  font-weight: 700;
  color: #f6c453;
  margin-bottom: 4rpx;
}
.rules-item {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  line-height: 1.6;
}

.bottom-spacer {
  height: 180rpx;
}

.cta-bar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 16rpx 32rpx calc(16rpx + env(safe-area-inset-bottom));
  background: rgba(11, 18, 32, 0.95);
  border-top: 1rpx solid rgba(255, 255, 255, 0.06);
  z-index: 50;
}
.cta-btn {
  padding: 22rpx 0;
  text-align: center;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.cta-btn-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.cta-btn-disabled {
  opacity: 0.5;
}
.cta-btn-text {
  font-size: 28rpx;
  font-weight: 700;
  color: #fff;
}
</style>
