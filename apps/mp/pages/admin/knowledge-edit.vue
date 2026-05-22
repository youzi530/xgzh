<script setup lang="ts">
/**
 * Admin 知识库文章编辑页 (Sprint 11 FE-S11-D06).
 *
 * 路由: ``/pages/admin/knowledge-edit`` (新建) 或 ``?article_id=xxx`` (编辑)
 *
 * 功能 (拍板: 基础 Markdown 编辑, 无富文本):
 * - 表单: slug (新建可填, 编辑只读) / title / category / level / tags / source
 * - textarea: content_md (大区域, monospace 字体)
 * - 高级字段折叠: source_url, legal_disclaimer
 * - 切换 is_published (publish/draft)
 * - 删除 (二次确认)
 */

import { onLoad } from '@dcloudio/uni-app'
import { storeToRefs } from 'pinia'
import { computed, ref } from 'vue'

import {
  createAdminArticle,
  deleteAdminArticle,
  getAdminArticleDetail,
  parseAdminKnowledgeError,
  updateAdminArticle,
  type KnowledgeArticleAdminDetail,
  type KnowledgeArticleCreatePayload,
  type KnowledgeArticleUpdatePayload,
  type KnowledgeCategory,
} from '@/api/admin-knowledge'
import { useAuthStore } from '@/stores/auth'
import { useThemeStore } from '@/stores/theme'

const __theme = useThemeStore() // eslint-disable-line @typescript-eslint/no-unused-vars

const authStore = useAuthStore()
const { isAdmin } = storeToRefs(authStore)

const isEditMode = ref<boolean>(false)
const articleId = ref<string | null>(null)
const original = ref<KnowledgeArticleAdminDetail | null>(null)
const phase = ref<'loading' | 'ready' | 'saving' | 'error'>('ready')
const advancedOpen = ref<boolean>(false)

// form fields
const formSlug = ref<string>('')
const formTitle = ref<string>('')
const formCategory = ref<KnowledgeCategory>('general')
const formLevel = ref<1 | 2 | 3>(1)
const formTags = ref<string>('') // comma-separated
const formContentMd = ref<string>('')
const formIsPublished = ref<boolean>(false)
const formSourceUrl = ref<string>('')
const formLegalDisclaimer = ref<string>('')

const categoryOptions: { label: string; value: KnowledgeCategory }[] = [
  { label: '港股打新', value: 'hk' },
  { label: 'A 股', value: 'cn' },
  { label: '通用', value: 'general' },
]

const levelOptions: { label: string; value: 1 | 2 | 3 }[] = [
  { label: '入门', value: 1 },
  { label: '进阶', value: 2 },
  { label: '实战', value: 3 },
]

const pageTitle = computed(() => (isEditMode.value ? '编辑文章' : '新建文章'))

function tagsToList(s: string): string[] | null {
  const trimmed = s
    .split(/[,,]/)
    .map((t) => t.trim())
    .filter(Boolean)
  return trimmed.length === 0 ? null : trimmed
}

function tagsFromList(list: string[] | null | undefined): string {
  return list ? list.join(', ') : ''
}

async function loadDetail(id: string) {
  phase.value = 'loading'
  try {
    const data = await getAdminArticleDetail(id)
    original.value = data
    formSlug.value = data.slug
    formTitle.value = data.title
    formCategory.value = data.category
    formLevel.value = data.level as 1 | 2 | 3
    formTags.value = tagsFromList(data.tags)
    formContentMd.value = data.content_md
    formIsPublished.value = data.is_published
    formSourceUrl.value = data.source_url ?? ''
    formLegalDisclaimer.value = data.legal_disclaimer ?? ''
    phase.value = 'ready'
  } catch (err) {
    const { code, message } = parseAdminKnowledgeError(err)
    if (code === 'article_not_found') {
      uni.showToast({ title: '文章不存在', icon: 'none' })
    } else {
      uni.showToast({ title: message, icon: 'none' })
    }
    setTimeout(() => uni.navigateBack(), 800)
  }
}

function validate(): string | null {
  if (!formSlug.value.trim()) return 'slug 不能为空'
  if (!/^[a-z0-9][a-z0-9-_]*$/.test(formSlug.value))
    return 'slug 只能包含小写字母/数字/-/_, 首字符必须字母数字'
  if (!formTitle.value.trim()) return '标题不能为空'
  if (formTitle.value.length > 128) return '标题最长 128 字'
  if (!formContentMd.value.trim()) return '正文不能为空'
  if (formContentMd.value.length > 200_000) return '正文最长 200,000 字'
  return null
}

async function onSave() {
  const errMsg = validate()
  if (errMsg) {
    uni.showToast({ title: errMsg, icon: 'none' })
    return
  }

  phase.value = 'saving'
  try {
    if (isEditMode.value && articleId.value) {
      const patch: KnowledgeArticleUpdatePayload = {
        title: formTitle.value.trim(),
        category: formCategory.value,
        level: formLevel.value,
        tags: tagsToList(formTags.value),
        content_md: formContentMd.value,
        is_published: formIsPublished.value,
        source_url: formSourceUrl.value.trim() || null,
        legal_disclaimer: formLegalDisclaimer.value.trim() || null,
      }
      original.value = await updateAdminArticle(articleId.value, patch)
      uni.showToast({ title: '已保存', icon: 'success' })
    } else {
      const payload: KnowledgeArticleCreatePayload = {
        slug: formSlug.value.trim(),
        title: formTitle.value.trim(),
        category: formCategory.value,
        level: formLevel.value,
        tags: tagsToList(formTags.value),
        content_md: formContentMd.value,
        is_published: formIsPublished.value,
        source: 'curated',
        source_url: formSourceUrl.value.trim() || null,
        legal_disclaimer: formLegalDisclaimer.value.trim() || null,
      }
      const created = await createAdminArticle(payload)
      uni.showToast({ title: '已创建', icon: 'success' })
      setTimeout(() => {
        uni.redirectTo({
          url: `/pages/admin/knowledge-edit?article_id=${encodeURIComponent(created.id)}`,
        })
      }, 600)
    }
  } catch (err) {
    const { code, message } = parseAdminKnowledgeError(err)
    if (code === 'slug_taken') {
      uni.showToast({ title: 'slug 已被占用', icon: 'none' })
    } else {
      uni.showToast({ title: message || '保存失败', icon: 'none' })
    }
  } finally {
    phase.value = 'ready'
  }
}

async function onDelete() {
  if (!articleId.value) return
  const confirm = await new Promise<boolean>((resolve) =>
    uni.showModal({
      title: '确认删除',
      content: '确认硬删此文章? 此操作不可恢复, view_count 也会一并清掉.',
      confirmText: '确认删除',
      confirmColor: '#ef4444',
      success: (r) => resolve(!!r.confirm),
      fail: () => resolve(false),
    }),
  )
  if (!confirm) return

  phase.value = 'saving'
  try {
    await deleteAdminArticle(articleId.value)
    uni.showToast({ title: '已删除', icon: 'success' })
    setTimeout(() => uni.navigateBack(), 600)
  } catch (err) {
    const { message } = parseAdminKnowledgeError(err)
    uni.showToast({ title: message || '删除失败', icon: 'none' })
    phase.value = 'ready'
  }
}

onLoad(async (query: Record<string, string | undefined> | undefined) => {
  if (!isAdmin.value) {
    uni.showToast({ title: '权限不足', icon: 'none' })
    setTimeout(() => uni.switchTab({ url: '/pages/me/index' }), 500)
    return
  }
  uni.setNavigationBarTitle({ title: pageTitle.value })
  if (query?.article_id) {
    isEditMode.value = true
    articleId.value = query.article_id
    await loadDetail(query.article_id)
    uni.setNavigationBarTitle({ title: pageTitle.value })
  }
})
</script>

<template>
  <view class="page">
    <view v-if="phase === 'loading'" class="state">
      <text>加载中...</text>
    </view>

    <view v-else class="content">
      <!-- Basic fields -->
      <view class="section">
        <view class="form-row">
          <view class="form-label">
            <text>slug</text>
            <text class="form-hint">URL key, 不可改</text>
          </view>
          <input
            v-model="formSlug"
            class="form-input"
            :class="{ disabled: isEditMode }"
            :disabled="isEditMode"
            placeholder="hk-subscription-key-dates"
            maxlength="64"
          />
        </view>

        <view class="form-row">
          <view class="form-label">
            <text>标题</text>
          </view>
          <input
            v-model="formTitle"
            class="form-input"
            placeholder="文章标题"
            maxlength="128"
          />
        </view>

        <view class="form-row">
          <view class="form-label">
            <text>分类</text>
          </view>
          <view class="chips">
            <view
              v-for="opt in categoryOptions"
              :key="opt.value"
              class="chip"
              :class="{ 'chip-active': formCategory === opt.value }"
              @tap="formCategory = opt.value"
            >
              <text>{{ opt.label }}</text>
            </view>
          </view>
        </view>

        <view class="form-row">
          <view class="form-label">
            <text>难度</text>
          </view>
          <view class="chips">
            <view
              v-for="opt in levelOptions"
              :key="opt.value"
              class="chip"
              :class="{ 'chip-active': formLevel === opt.value }"
              @tap="formLevel = opt.value"
            >
              <text>{{ opt.label }}</text>
            </view>
          </view>
        </view>

        <view class="form-row">
          <view class="form-label">
            <text>标签</text>
            <text class="form-hint">逗号分隔</text>
          </view>
          <input
            v-model="formTags"
            class="form-input"
            placeholder="入门, 港股, 新手必看"
          />
        </view>

        <view class="form-row">
          <view class="form-label">
            <text>状态</text>
          </view>
          <view class="chips">
            <view
              class="chip"
              :class="{ 'chip-active': !formIsPublished }"
              @tap="formIsPublished = false"
            >
              <text>草稿</text>
            </view>
            <view
              class="chip"
              :class="{ 'chip-active chip-published': formIsPublished }"
              @tap="formIsPublished = true"
            >
              <text>已发布</text>
            </view>
          </view>
        </view>
      </view>

      <!-- Content (markdown) -->
      <view class="section">
        <view class="section-title">
          <text>正文 (Markdown)</text>
        </view>
        <textarea
          v-model="formContentMd"
          class="textarea content-textarea"
          placeholder="# 标题\n\n正文 markdown..."
          maxlength="200000"
        />
        <view class="markdown-hint">
          <text>支持基础 Markdown 语法; FE 详情页用前后端共享渲染器</text>
        </view>
      </view>

      <!-- Advanced -->
      <view class="section">
        <view class="section-title-toggle" @tap="advancedOpen = !advancedOpen">
          <text>{{ advancedOpen ? '▼' : '▶' }} 高级字段</text>
        </view>
        <view v-if="advancedOpen">
          <view class="form-row">
            <view class="form-label">
              <text>原文 URL</text>
              <text class="form-hint">爬虫来源 / 引用</text>
            </view>
            <input
              v-model="formSourceUrl"
              class="form-input"
              placeholder="https://..."
              maxlength="2048"
            />
          </view>

          <view class="form-row">
            <view class="form-label">
              <text>免责声明</text>
              <text class="form-hint">底部追加</text>
            </view>
            <textarea
              v-model="formLegalDisclaimer"
              class="textarea small-textarea"
              placeholder="本文内容仅供参考,不构成投资建议"
              maxlength="2000"
            />
          </view>
        </view>
      </view>

      <!-- Actions -->
      <view class="actions">
        <view class="save-btn" @tap="onSave">
          <text>{{ isEditMode ? '保存修改' : '创建文章' }}</text>
        </view>
        <view v-if="isEditMode" class="delete-btn" @tap="onDelete">
          <text>删除文章</text>
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

.section-title-toggle {
  padding: 8rpx 0;

  text {
    color: #93c5fd;
    font-size: 26rpx;
  }
}

.form-row {
  margin-bottom: 20rpx;
}

.form-label {
  display: flex;
  align-items: baseline;
  gap: 12rpx;
  margin-bottom: 8rpx;

  text {
    color: #93c5fd;
    font-size: 26rpx;
  }

  .form-hint {
    color: #6b7794;
    font-size: 22rpx;
  }
}

.form-input {
  width: 100%;
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;
  color: #e4e7ee;
  font-size: 26rpx;
  box-sizing: border-box;

  &.disabled {
    color: #6b7794;
    background-color: #1a2238;
  }
}

.textarea {
  width: 100%;
  padding: 16rpx 20rpx;
  background-color: #0b1220;
  border: 1rpx solid #2a3654;
  border-radius: 12rpx;
  color: #e4e7ee;
  font-size: 26rpx;
  box-sizing: border-box;
  font-family: monospace;
}

.content-textarea {
  min-height: 600rpx;
}

.small-textarea {
  min-height: 120rpx;
}

.markdown-hint {
  margin-top: 8rpx;

  text {
    color: #6b7794;
    font-size: 22rpx;
  }
}

.chips {
  display: flex;
  flex-wrap: wrap;
  gap: 12rpx;
}

.chip {
  padding: 10rpx 22rpx;
  border-radius: 32rpx;
  border: 1rpx solid #2a3654;
  background-color: #1a2238;

  text {
    font-size: 24rpx;
    color: #8b9bb8;
  }
}

.chip-active {
  border-color: #3b82f6;
  background-color: rgba(59, 130, 246, 0.18);

  text {
    color: #93c5fd;
  }
}

.chip-published {
  border-color: #22c55e;
  background-color: rgba(34, 197, 94, 0.18);

  text {
    color: #86efac;
  }
}

.actions {
  margin-top: 32rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}

.save-btn {
  padding: 24rpx;
  background-color: #3b82f6;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #ffffff;
    font-size: 28rpx;
    font-weight: 600;
  }
}

.delete-btn {
  padding: 24rpx;
  background-color: rgba(239, 68, 68, 0.18);
  border: 1rpx solid #ef4444;
  border-radius: 16rpx;
  text-align: center;

  text {
    color: #fca5a5;
    font-size: 28rpx;
  }
}
</style>
