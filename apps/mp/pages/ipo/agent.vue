<script setup lang="ts">
import { onLoad } from '@dcloudio/uni-app'
import { ref } from 'vue'

import { diagnoseStream } from '@/api/agent'

const code = ref('')
const name = ref('')
const question = ref('')
const output = ref('')
const meta = ref<{ found_in_source: boolean } | null>(null)
const streaming = ref(false)
const errMsg = ref('')

onLoad((query) => {
  code.value = decodeURIComponent((query?.code as string) ?? '')
  name.value = decodeURIComponent((query?.name as string) ?? '')
  if (code.value) startDiagnose()
})

async function startDiagnose() {
  if (streaming.value) return
  output.value = ''
  errMsg.value = ''
  meta.value = null
  streaming.value = true

  await diagnoseStream(
    {
      code: code.value,
      name: name.value || undefined,
      question: question.value || undefined,
    },
    {
      onStart: (m) => {
        meta.value = { found_in_source: m.found_in_source }
      },
      onDelta: (text) => {
        output.value += text
      },
      onEnd: () => {
        streaming.value = false
      },
      onError: (err) => {
        errMsg.value = err.message
        streaming.value = false
      },
    },
  )
}
</script>

<template>
  <view class="page">
    <view class="banner">
      <text class="banner-text">AI 输出仅供参考，不构成投资建议</text>
    </view>

    <view class="header">
      <text class="title">{{ name || code }}</text>
      <text class="sub">{{ code }} · DeepSeek-V3</text>
      <text v-if="meta && !meta.found_in_source" class="warn">
        ⚠️ 数据源未命中该代码，AI 将基于通用知识作答
      </text>
    </view>

    <view class="qa-box">
      <input
        v-model="question"
        class="qa-input"
        type="text"
        placeholder="可选：输入具体问题（留空则做基础诊断）"
        :disabled="streaming"
      />
      <view :class="['qa-btn', streaming && 'qa-btn-disabled']" @tap="startDiagnose">
        {{ streaming ? '生成中…' : '重新诊断' }}
      </view>
    </view>

    <view class="output">
      <text v-if="!output && !errMsg && !streaming" class="placeholder">
        点击「重新诊断」开始
      </text>
      <text v-if="output" class="output-text">{{ output }}</text>
      <view v-if="streaming" class="cursor">▋</view>
      <view v-if="errMsg" class="error">⚠️ {{ errMsg }}</view>
    </view>

    <view class="disclaimer">
      本内容由大语言模型生成，可能存在错误或滞后；最终以官方招股书 / 公告为准。
    </view>
  </view>
</template>

<style lang="scss" scoped>
.page {
  min-height: 100vh;
  padding: 0 24rpx 80rpx;
}
.banner {
  position: sticky;
  top: 0;
  z-index: 10;
  margin: 0 -24rpx 16rpx;
  padding: 12rpx 24rpx;
  background: rgba(246, 196, 83, 0.12);
  border-bottom: 1rpx solid rgba(246, 196, 83, 0.32);
}
.banner-text {
  font-size: 22rpx;
  color: var(--color-accent);
}
.header {
  margin: 16rpx 0 24rpx;
}
.title {
  display: block;
  font-size: 36rpx;
  font-weight: 700;
}
.sub {
  display: block;
  margin-top: 4rpx;
  font-size: 22rpx;
  color: var(--color-text-muted);
}
.warn {
  display: block;
  margin-top: 12rpx;
  font-size: 22rpx;
  color: var(--color-accent);
}
.qa-box {
  display: flex;
  gap: 16rpx;
  align-items: center;
  margin-bottom: 24rpx;
}
.qa-input {
  flex: 1;
  padding: 16rpx 20rpx;
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 12rpx;
  color: var(--color-text);
  font-size: 26rpx;
}
.qa-btn {
  padding: 16rpx 28rpx;
  border-radius: 12rpx;
  background: var(--color-primary);
  color: #fff;
  font-size: 26rpx;
}
.qa-btn-disabled {
  opacity: 0.5;
}
.output {
  min-height: 400rpx;
  padding: 24rpx;
  background: var(--color-surface);
  border: 1rpx solid var(--color-border);
  border-radius: 16rpx;
}
.placeholder {
  color: var(--color-text-muted);
  font-size: 26rpx;
}
.output-text {
  font-size: 28rpx;
  line-height: 1.7;
  color: var(--color-text);
  white-space: pre-wrap;
  word-break: break-word;
}
.cursor {
  display: inline-block;
  margin-left: 4rpx;
  color: var(--color-primary);
  animation: blink 1s steps(2, end) infinite;
}
.error {
  margin-top: 16rpx;
  color: var(--color-danger);
  font-size: 26rpx;
}
.disclaimer {
  margin-top: 24rpx;
  text-align: center;
  font-size: 22rpx;
  color: var(--color-text-muted);
}
@keyframes blink {
  to {
    opacity: 0;
  }
}
</style>
