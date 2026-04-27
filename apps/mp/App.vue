<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'

onLaunch(() => {
  console.log('App Launched')
})
</script>

<style lang="scss">
/* XGZH 全局样式
 *
 * CSS 变量定义同时挂在 ``:root`` (H5) + ``page`` (小程序) 上:
 * - 小程序 wxss 没有 ``:root`` 选择器, 顶层是 ``page``; 只写 ``:root`` 会让所有
 *   ``var(--color-*)`` 在小程序端 fallback 到 ``initial`` (透明/黑), 表现为
 *   "白底白字"、tab 选中后看不见等 UI 灾难。
 * - H5 上 uni-app 把 ``page`` 编译成 ``uni-page-body``, 不是文档根, ``var`` 继承
 *   范围有限 (例如某些 portal/teleport 出来的元素拿不到), 所以 ``:root`` 兜一份。
 */
page,
:root {
  --color-bg: #0b1220;
  --color-surface: #131a2c;
  --color-surface-elevated: #1a2238;
  --color-text: #e2e8f0;
  --color-text-muted: #94a3b8;
  --color-primary: #4f8bff;
  --color-accent: #f6c453;
  --color-success: #22c55e;
  --color-danger: #ef4444;
  --color-border: rgba(255, 255, 255, 0.06);
}

page {
  background: var(--color-bg);
  color: var(--color-text);
  font-family:
    -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue', sans-serif;
}
</style>
