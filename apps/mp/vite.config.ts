import { cpSync, existsSync } from 'node:fs'
import path from 'node:path'

import uni from '@dcloudio/vite-plugin-uni'
import { defineConfig, type Plugin } from 'vite'

/**
 * BUG-S6.6-001: 强制把 ``apps/mp/static/`` 同步复制到产物目录.
 *
 * 背景: ``@dcloudio/vite-plugin-uni`` 内部的 ``uniCopyPlugin`` 在某些场景下不会触发
 * (实测 macOS 上 ``UNI_INPUT_DIR=.`` 作根目录, 任何带新增 static 子目录的 dev/build
 * 都漏复制 → ``dist/dev/mp-weixin/static/`` 整目录不存在 → 小程序启动报
 * "tabBar.iconPath: static/tabbar/home-normal.png 未找到").
 *
 * 防御方案: 在 ``buildEnd`` (build) 和 ``writeBundle`` 末尾再 cp 一次, 用 Node
 * ``fs.cpSync(src, dest, { recursive: true })`` 整目录同步, 不走 chokidar (避开 race).
 * dev/watch 也走这个 hook (vite watcher 在每次重 bundle 后触发 ``writeBundle``),
 * 所以 watch 模式下增删 static/ 文件也会被同步.
 *
 * 关键决策:
 * - 不删 ``uni()`` 内置 copy: 万一某些版本 uni 修好了, 这个 fallback 也是幂等 cp,
 *   多复制一次 PNG 没成本, 文件相同 cp 是 no-op.
 * - 用 sync API 不用 async: writeBundle 是 vite 同步收尾钩子, 用 sync 保证退出前必完成.
 * - 不依赖外部包 (vite-plugin-static-copy): 减少依赖面, 只用 Node 原生 fs.
 */
function forceCopyStatic(): Plugin {
  return {
    name: 'xgzh:force-copy-static',
    apply: () => true, // dev + build 都跑
    writeBundle(options) {
      const root = path.resolve(__dirname)
      const src = path.join(root, 'static')
      if (!existsSync(src)) return
      const outDir = options.dir
      if (!outDir) return
      const dest = path.join(outDir, 'static')
      try {
        cpSync(src, dest, { recursive: true, force: true })
      } catch (err) {
        console.warn('[xgzh:force-copy-static] cp failed:', err)
      }
    },
    closeBundle() {
      // dev (watch) 用 closeBundle 兜底, 因为部分 vite 配置下 writeBundle 的 options.dir 为空
      const root = path.resolve(__dirname)
      const src = path.join(root, 'static')
      if (!existsSync(src)) return
      const platform = process.env.UNI_PLATFORM || 'mp-weixin'
      const mode = process.env.NODE_ENV === 'production' ? 'build' : 'dev'
      const dest = path.join(root, 'dist', mode, platform, 'static')
      try {
        cpSync(src, dest, { recursive: true, force: true })
      } catch (err) {
        console.warn('[xgzh:force-copy-static] closeBundle cp failed:', err)
      }
    },
  }
}

export default defineConfig({
  plugins: [uni(), forceCopyStatic()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, '.'),
    },
  },
  // 注: uniapp 不读 vite.config.ts 的 server.* 配置, 真生效的是 manifest.json 里
  // 的 h5.devServer (port / host / proxy). 想改 H5 dev port 或 proxy target, 去
  // apps/mp/manifest.json 改. 这里留空给 vite 默认即可.
})
