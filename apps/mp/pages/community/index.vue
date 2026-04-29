<script setup lang="ts">
/**
 * 社区 tab 主页 (FE-S6-005 接 BE-S6-006).
 *
 * 路由: /pages/community/index  (tabBar 第 4 槽位)
 *
 * 视图层次:
 * - hero: 标题 + "+ 发帖"  (未登录拦到登录)
 * - 分类 chip (全部 / 综合 / 新股讨论 / 经验分享)
 * - feed: 卡片列表 (作者头像 / 昵称 / 时间 / 内容 / IPO chip / 点赞数 / 评论数)
 * - 空态: feed 暂无内容时显引导
 *
 * 设计要点:
 *
 * - **匿名可读**: 列表 / 详情走 ``skipAuth: true``; 发帖 / 点赞 / 评论强制登录
 * - **下拉刷新 / 触底加载**: ``onPullDownRefresh`` (mp 自带) / 滚动接近底部 +1 page
 * - **暗色 token 沿用**: 与其它 tab 一致
 * - **rejected 帖不在 feed**: 后端只返 status=published; 作者自己看 rejected 走 detail 页
 * - **floating "+ 发帖" 按钮**: 右下角圆形 FAB; 点击未登录拦到 ``/pages/auth/login``
 */

import { onPullDownRefresh, onShow } from '@dcloudio/uni-app'
import { computed, ref } from 'vue'

import {
  type PostCategory,
  type PostDetail,
  listPosts,
  parseCommunityError,
} from '@/api/community'
import { readAccessTokenSync } from '@/stores/auth'

interface CategoryOption {
  key: 'all' | PostCategory
  label: string
}

const CATEGORIES: CategoryOption[] = [
  { key: 'all', label: '全部' },
  { key: 'general', label: '综合' },
  { key: 'ipo_discuss', label: '新股讨论' },
  { key: 'experience', label: '经验分享' },
]

const posts = ref<PostDetail[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 20
const selectedCategory = ref<'all' | PostCategory>('all')
const loading = ref(false)
const loadingMore = ref(false)
const hasMore = computed(() => posts.value.length < total.value)

async function refresh(reset = true) {
  if (reset) {
    page.value = 1
    posts.value = []
    total.value = 0
  }
  loading.value = true
  try {
    const res = await listPosts({
      category: selectedCategory.value === 'all' ? undefined : selectedCategory.value,
      page: page.value,
      page_size: pageSize,
    })
    if (reset) {
      posts.value = res.items
    } else {
      posts.value = [...posts.value, ...res.items]
    }
    total.value = res.total
  } catch (err) {
    const e = parseCommunityError(err)
    uni.showToast({ title: e.message, icon: 'none' })
  } finally {
    loading.value = false
    uni.stopPullDownRefresh()
  }
}

async function loadMore() {
  if (loadingMore.value || !hasMore.value) return
  loadingMore.value = true
  page.value += 1
  try {
    await refresh(false)
  } finally {
    loadingMore.value = false
  }
}

onShow(() => {
  void refresh(true)
})

onPullDownRefresh(() => {
  void refresh(true)
})

function selectCategory(cat: 'all' | PostCategory) {
  if (selectedCategory.value === cat) return
  selectedCategory.value = cat
  void refresh(true)
}

function gotoEdit() {
  if (readAccessTokenSync() === null) {
    uni.redirectTo({ url: '/pages/auth/login' })
    return
  }
  uni.navigateTo({
    url: '/pages/community/edit',
    fail: () => uni.showToast({ title: '发帖页即将上线', icon: 'none' }),
  })
}

function gotoDetail(postId: string) {
  uni.navigateTo({
    url: `/pages/community/detail?id=${postId}`,
    fail: () => uni.showToast({ title: '详情页即将上线', icon: 'none' })
  })
}

/**
 * BUG-S6.8-003: 点击作者昵称 / 头像 → 跳用户公开主页.
 *
 * ``stop-propagation``: 卡片整体绑了 ``gotoDetail``, 这里点的是子元素 (头像 / 昵称),
 * 必须阻止冒泡, 否则会先跳详情页再覆盖跳主页 = 双跳栈污染.
 */
function gotoUserProfile(userId: string | null | undefined, ev: Event) {
  ev.stopPropagation?.()
  if (!userId) {
    uni.showToast({ title: '该用户已注销', icon: 'none' })
    return
  }
  uni.navigateTo({
    url: `/pages/user/profile?id=${encodeURIComponent(userId)}`,
    fail: () => uni.showToast({ title: '主页加载失败', icon: 'none' }),
  })
}

function formatRelativeTime(iso: string): string {
  if (!iso) return ''
  const t = new Date(iso).getTime()
  if (Number.isNaN(t)) return ''
  const diff = Date.now() - t
  const min = 60 * 1000
  const hour = 60 * min
  const day = 24 * hour
  if (diff < min) return '刚刚'
  if (diff < hour) return `${Math.floor(diff / min)}分钟前`
  if (diff < day) return `${Math.floor(diff / hour)}小时前`
  if (diff < 7 * day) return `${Math.floor(diff / day)}天前`
  const d = new Date(t)
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, '0')}`
}

function avatarFallback(post: PostDetail): string {
  const name = post.user_nickname || '?'
  return name.slice(0, 1)
}

function categoryChipLabel(cat: PostCategory): string {
  if (cat === 'ipo_discuss') return '新股'
  if (cat === 'experience') return '经验'
  return ''
}
</script>

<template>
  <view class="page">
    <!-- Hero -->
    <view class="hero">
      <view class="hero-text">
        <text class="hero-title">打新社区</text>
        <text class="hero-subtitle">分享中签 / 经验 / 行情解读</text>
      </view>
    </view>

    <!-- 分类 chip -->
    <scroll-view class="cat-scroll" scroll-x show-scrollbar="false">
      <view class="cat-row">
        <view
          v-for="c in CATEGORIES"
          :key="c.key"
          class="cat-chip"
          :class="{ 'cat-chip-active': selectedCategory === c.key }"
          @tap="selectCategory(c.key)"
        >
          <text class="cat-chip-text">{{ c.label }}</text>
        </view>
      </view>
    </scroll-view>

    <!-- feed -->
    <view class="feed">
      <view v-if="posts.length === 0 && !loading" class="empty">
        <text class="empty-emoji">💬</text>
        <text class="empty-title">暂无内容</text>
        <text class="empty-desc">来发布第一条动态吧</text>
        <view
          class="empty-btn"
          hover-class="empty-btn-hover"
          :hover-stay-time="80"
          @tap="gotoEdit"
        >
          <text class="empty-btn-text">+ 发帖</text>
        </view>
      </view>

      <view
        v-for="p in posts"
        v-else
        :key="p.id"
        class="card"
        hover-class="card-hover"
        :hover-stay-time="80"
        @tap="gotoDetail(p.id)"
      >
        <view class="card-head">
          <view
            class="avatar"
            hover-class="avatar-hover"
            :hover-stay-time="60"
            @tap.stop="(ev: Event) => gotoUserProfile(p.user_id, ev)"
          >
            <image
              v-if="p.user_avatar_url"
              class="avatar-img"
              :src="p.user_avatar_url"
              mode="aspectFill"
            />
            <text v-else class="avatar-fallback">{{ avatarFallback(p) }}</text>
          </view>
          <view class="card-meta">
            <text
              class="card-nickname clickable"
              hover-class="nickname-hover"
              :hover-stay-time="60"
              @tap.stop="(ev: Event) => gotoUserProfile(p.user_id, ev)"
            >{{ p.user_nickname || '匿名用户' }}</text>
            <text class="card-time">{{ formatRelativeTime(p.created_at) }}</text>
          </view>
          <text v-if="categoryChipLabel(p.category)" class="card-cat-chip">
            {{ categoryChipLabel(p.category) }}
          </text>
        </view>

        <text class="card-content">{{ p.content }}</text>

        <view v-if="p.related_ipo_code" class="ipo-chip">
          <text class="ipo-chip-text">关联 {{ p.related_ipo_code }}</text>
        </view>

        <view class="card-foot">
          <view class="foot-item">
            <text class="foot-icon">{{ p.is_liked ? '❤️' : '🤍' }}</text>
            <text class="foot-text">{{ p.likes_count }}</text>
          </view>
          <view class="foot-item">
            <text class="foot-icon">💬</text>
            <text class="foot-text">{{ p.comments_count }}</text>
          </view>
        </view>
      </view>

      <!-- 触底加载 -->
      <view
        v-if="hasMore && posts.length > 0"
        class="load-more"
        hover-class="load-more-hover"
        :hover-stay-time="80"
        @tap="loadMore"
      >
        <text class="load-more-text">{{ loadingMore ? '加载中...' : '加载更多' }}</text>
      </view>

      <view v-else-if="posts.length > 0" class="end-text">— 没有更多了 —</view>
    </view>

    <!-- floating + button -->
    <view class="fab" hover-class="fab-hover" :hover-stay-time="80" @tap="gotoEdit">
      <text class="fab-text">+</text>
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 32rpx 32rpx 0;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
  gap: 24rpx;
}

.hero {
  display: flex;
  flex-direction: column;
  gap: 8rpx;
}
.hero-text {
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.hero-title {
  font-size: 40rpx;
  font-weight: 700;
}
.hero-subtitle {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}

// ─── 分类 chip ──────────────────────────────────────────────
.cat-scroll {
  width: 100%;
  white-space: nowrap;
}
.cat-row {
  display: inline-flex;
  gap: 16rpx;
  padding: 4rpx 0;
}
.cat-chip {
  padding: 14rpx 28rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.08));
  border-radius: 999rpx;
}
.cat-chip-active {
  background: rgba(79, 139, 255, 0.15);
  border-color: rgba(79, 139, 255, 0.5);
}
.cat-chip-text {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}

// ─── feed ────────────────────────────────────────────────────
.feed {
  display: flex;
  flex-direction: column;
  gap: 16rpx;
  padding-bottom: 160rpx;
}

.empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12rpx;
  padding: 80rpx 32rpx;
  background: var(--color-surface, #131a2c);
  border-radius: 24rpx;
  border: 1rpx dashed rgba(255, 255, 255, 0.1);
}
.empty-emoji {
  font-size: 80rpx;
}
.empty-title {
  font-size: 30rpx;
  font-weight: 600;
}
.empty-desc {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
  margin-bottom: 16rpx;
}
.empty-btn {
  padding: 22rpx 48rpx;
  border-radius: 999rpx;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
}
.empty-btn-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.empty-btn-text {
  font-size: 26rpx;
  color: #fff;
  font-weight: 700;
}

.card {
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 20rpx;
  padding: 24rpx;
  display: flex;
  flex-direction: column;
  gap: 16rpx;
}
.card-hover {
  background: rgba(255, 255, 255, 0.04);
}

.card-head {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 16rpx;
}
.avatar {
  width: 64rpx;
  height: 64rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  overflow: hidden;
}
.avatar-img {
  width: 100%;
  height: 100%;
}
.avatar-fallback {
  color: #fff;
  font-size: 26rpx;
  font-weight: 700;
}
/* BUG-S6.8-003: 头像 / 昵称点击主页 — hover 弱反馈 (避免误以为不可点) */
.avatar-hover {
  opacity: 0.7;
}
.clickable {
  /* H5 hint, 小程序无 cursor; 主要靠 hover-class 反馈 */
  cursor: pointer;
}
.nickname-hover {
  opacity: 0.7;
}
.card-meta {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4rpx;
}
.card-nickname {
  font-size: 26rpx;
  font-weight: 600;
}
.card-time {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}
.card-cat-chip {
  flex-shrink: 0;
  font-size: 22rpx;
  padding: 4rpx 12rpx;
  border-radius: 6rpx;
  background: rgba(79, 139, 255, 0.12);
  color: #4f8bff;
}

.card-content {
  font-size: 28rpx;
  line-height: 1.6;
  color: var(--color-text, #e2e8f0);
  word-break: break-all;
}

.ipo-chip {
  align-self: flex-start;
  padding: 6rpx 16rpx;
  border-radius: 999rpx;
  background: rgba(246, 196, 83, 0.1);
  border: 1rpx solid rgba(246, 196, 83, 0.3);
}
.ipo-chip-text {
  font-size: 22rpx;
  color: #f6c453;
}

.card-foot {
  display: flex;
  flex-direction: row;
  gap: 32rpx;
  padding-top: 8rpx;
  border-top: 1rpx solid rgba(255, 255, 255, 0.04);
}
.foot-item {
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 8rpx;
}
.foot-icon {
  font-size: 24rpx;
}
.foot-text {
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
}

// ─── load more / end ──────────────────────────────────────
.load-more {
  padding: 24rpx;
  text-align: center;
  background: var(--color-surface, #131a2c);
  border-radius: 999rpx;
}
.load-more-hover {
  background: rgba(255, 255, 255, 0.04);
}
.load-more-text {
  font-size: 24rpx;
  color: var(--color-text-muted, #94a3b8);
}
.end-text {
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted, #94a3b8);
  padding: 24rpx;
  opacity: 0.6;
}

// ─── floating + button ─────────────────────────────────────
.fab {
  position: fixed;
  right: 32rpx;
  bottom: 48rpx;
  width: 100rpx;
  height: 100rpx;
  border-radius: 50%;
  background: linear-gradient(135deg, #4f8bff, #6e3df0);
  box-shadow: 0 8rpx 24rpx rgba(79, 139, 255, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.fab-hover {
  background: linear-gradient(135deg, #3a72e8, #5a30d4);
}
.fab-text {
  color: #fff;
  font-size: 56rpx;
  font-weight: 300;
  line-height: 1;
}
</style>
