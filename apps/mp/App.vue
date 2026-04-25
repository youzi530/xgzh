<script setup lang="ts">
import { onLaunch } from '@dcloudio/uni-app'

onLaunch(() => {
  console.log('App Launched')
})
</script>

<style lang="scss">
/* XGZH 全局样式 */
page {
  background: var(--color-bg);
  color: var(--color-text);
  font-family:
    -apple-system, BlinkMacSystemFont, 'PingFang SC', 'Helvetica Neue', sans-serif;
}

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
</style>
