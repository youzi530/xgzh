<script setup lang="ts">
import { useThemeStore as __useThemeStore } from '@/stores/theme'
const __theme = __useThemeStore()

/**
 * 文章原文 webview 中转页 (BUG-S7.2-004).
 *
 * 路由: ``/pages/article/webview?url=<encoded_url>``
 *
 * 设计目标
 * --------
 * Sprint 7.0–7.1 时文章详情页"查看原文"在 mp 端只能 setClipboard + showModal
 * 让用户复制链接到浏览器粘贴打开 — 用户体验差. 7.1 仅 H5 改用 ``window.open``
 * (浏览器主场), mp 仍 setClipboard 兜底. 7.2 用户复测要求 mp 也学 H5 直接打开
 * 新界面看内容, 本页就是这个中转页.
 *
 * 跨端合规
 * --------
 * - **mp-weixin**: ``<web-view>`` 必须把第三方域名加到"业务域名"白名单 (微信
 *   公众平台后台 mp.weixin.qq.com 手动加, 最多 300 个 + ICP 备案 + 24h). 调试器
 *   / 真机预览模式未配置仅显示警告**仍可加载**, 上线前用户在公众平台后台批量加.
 * - **mp.weixin.qq.com (微信公众号文章)**: 微信明确禁止小程序 webview 内打开
 *   (反垄断/防套娃). 本页检测 url 含此域名时**自动 fallback** 到 setClipboard +
 *   显示"请在浏览器中打开"提示.
 * - **App / H5**: 不会路由到本页 (article/detail.vue 已分支 ``#ifdef H5`` 走
 *   ``window.open``); 但保留兼容性, 本页 H5 端用 iframe (受 X-Frame-Options 约束).
 *
 * 加载失败兜底 (``@error``)
 * ------------------------
 * mp ``<web-view>`` 加载失败 (网络断 / 404 / 业务域名未配置且站点拒绝) 触发
 * ``@error`` 事件, 本页切换到 fallback 视图: "打开浏览器查看" + 复制链接按钮.
 */

import { onLoad } from '@dcloudio/uni-app'
import { ref } from 'vue'

import { getNavParam } from '@/utils/navigate'

const url = ref<string>('')
const error = ref<string>('')
const isWechatArticle = ref<boolean>(false)
const loadFailed = ref<boolean>(false)

function detectWechatArticle(u: string): boolean {
  // 微信公众号文章域名: mp.weixin.qq.com (主), wx.qq.com (短链), weixin.qq.com (有时)
  return /(?:mp\.weixin\.qq\.com|wx\.qq\.com|weixin\.qq\.com)/i.test(u)
}

function copyUrl() {
  if (!url.value) return
  uni.setClipboardData({
    data: url.value,
    success: () => uni.showToast({ title: '链接已复制', icon: 'success' }),
    fail: () => uni.showToast({ title: '复制失败', icon: 'none' }),
  })
}

function goBack() {
  uni.navigateBack({ fail: () => uni.reLaunch({ url: '/pages/article/index' }) })
}

function onWebviewError() {
  // mp/app 端 web-view 加载失败 (网络/域名未白名单/站点拒绝)
  loadFailed.value = true
  console.warn('[article-webview] web-view load failed', url.value)
}

onLoad((options) => {
  const raw = getNavParam(options, 'url')
  if (!raw) {
    error.value = '缺少 url 参数'
    return
  }
  if (detectWechatArticle(raw)) {
    // 微信公众号文章: 复制链接 + 提示在浏览器打开
    isWechatArticle.value = true
    url.value = raw
    uni.setClipboardData({
      data: raw,
      success: () => uni.showToast({ title: '链接已复制', icon: 'success' }),
      fail: () => {/* 复制失败也无所谓, 用户能在本页看到链接复制按钮 */},
    })
    return
  }
  url.value = raw
})
</script>

<template>
  <view :class="['page', __theme.themeClass]">
    <!-- ─── error: 缺参数 ─── -->
    <view v-if="error" class="state">
      <text class="state-emoji">😕</text>
      <text class="state-text">{{ error }}</text>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="goBack">
        <text class="state-cta-text">返回</text>
      </view>
    </view>

    <!-- ─── 微信公众号文章: 不能 webview, 显复制 + 浏览器打开 提示 ─── -->
    <view v-else-if="isWechatArticle" class="state">
      <text class="state-emoji">📋</text>
      <text class="state-title">微信公众号文章</text>
      <text class="state-desc">微信不支持在小程序内打开公众号文章. 链接已复制到剪贴板, 请在浏览器中粘贴打开.</text>
      <view class="state-link-box">
        <text class="state-link" selectable>{{ url }}</text>
      </view>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="copyUrl">
        <text class="state-cta-text">再次复制链接</text>
      </view>
      <view class="state-cta state-cta-secondary" hover-class="state-cta-hover" :hover-stay-time="80" @tap="goBack">
        <text class="state-cta-text-ghost">返回</text>
      </view>
    </view>

    <!-- ─── webview 加载失败: 复制 + 浏览器兜底 ─── -->
    <view v-else-if="loadFailed" class="state">
      <text class="state-emoji">🔌</text>
      <text class="state-title">原文加载失败</text>
      <text class="state-desc">可能是网络问题或站点暂时不可访问. 链接已复制, 可在浏览器中打开.</text>
      <view class="state-cta" hover-class="state-cta-hover" :hover-stay-time="80" @tap="copyUrl">
        <text class="state-cta-text">复制链接</text>
      </view>
      <view class="state-cta state-cta-secondary" hover-class="state-cta-hover" :hover-stay-time="80" @tap="goBack">
        <text class="state-cta-text-ghost">返回</text>
      </view>
    </view>

    <!-- ─── 正常 webview ─── -->
    <web-view v-else-if="url" :src="url" @error="onWebviewError" />
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  background: var(--color-bg, #0b1220);
  color: var(--color-text, #e2e8f0);
  display: flex;
  flex-direction: column;
}

.state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 24rpx;
  padding: 120rpx 48rpx;
  text-align: center;
}

.state-emoji {
  font-size: 96rpx;
  line-height: 1;
}

.state-title {
  font-size: 32rpx;
  font-weight: 800;
  color: var(--color-text, #e2e8f0);
}

.state-text {
  font-size: 28rpx;
  font-weight: 600;
  color: var(--color-text, #e2e8f0);
}

.state-desc {
  font-size: 24rpx;
  line-height: 1.6;
  color: var(--color-text-muted, #94a3b8);
  padding: 0 24rpx;
}

.state-link-box {
  width: 100%;
  margin-top: 8rpx;
  padding: 16rpx 20rpx;
  background: var(--color-surface, #131a2c);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.06));
  border-radius: 12rpx;
}

.state-link {
  font-size: 22rpx;
  color: var(--color-primary, #4f8bff);
  word-break: break-all;
  line-height: 1.5;
}

.state-cta {
  margin-top: 16rpx;
  padding: 20rpx 64rpx;
  border-radius: 999rpx;
  background: var(--color-primary, #4f8bff);
}

.state-cta-secondary {
  background: rgba(255, 255, 255, 0.06);
  border: 1rpx solid var(--color-border, rgba(255, 255, 255, 0.12));
}

.state-cta-hover {
  opacity: 0.8;
}

.state-cta-text {
  font-size: 26rpx;
  font-weight: 700;
  color: #ffffff;
}

.state-cta-text-ghost {
  font-size: 26rpx;
  color: var(--color-text, #e2e8f0);
}
</style>
