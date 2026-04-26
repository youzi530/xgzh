/**
 * 打字机节流调度器 (FE-S2-002).
 *
 * 用途
 * ====
 * LLM SSE delta 流速波动大 (突发 100 token/s, 慢时 5 token/s); 直接逐 delta
 * mutate ``message.content`` 会让 Vue 在一帧内触发多次响应式更新 — 虽然 Vue
 * 自带 batching, 但**重 parse markdown** 仍每次都会跑, MP 上是显著开销 (~1.5ms × N).
 *
 * 解法: 把多次 ``push(text)`` 在 16ms 一帧内合并, 帧到点 flush 一次给 ``commit``.
 *
 * 跨端实现
 * ========
 * - H5 ``requestAnimationFrame`` 60fps 自动对齐刷新; 后台 tab 自动暂停 (省电)
 * - MP / App 没有 rAF 全局, 退化为 ``setTimeout(16ms)``; 频率精度差一点点 (~17ms)
 *   但对打字机效果完全够用
 *
 * 边界
 * ====
 * - **流结束时 ``drain()``**: 把 buffer 一次性 flush, 避免最后几个字符卡在 buffer 里
 * - **错误 / cancel 也 ``drain()``**: 让用户看到流断之前的全部内容
 * - **不支持暂停/恢复**: 这是单调推进的 schedule, 一旦 push 就承诺会 flush
 *
 * 使用模式
 * ========
 * ```ts
 * const tw = new Typewriter((text) => message.content += text)
 * sse.onDelta((t) => tw.push(t))
 * sse.onEnd(() => tw.drain())
 * sse.onError(() => tw.drain())
 * ```
 */

type CommitFn = (text: string) => void

/** 跨端 rAF: H5 用浏览器原生, 其它端 polyfill 16ms setTimeout */
function scheduleFrame(cb: () => void): () => void {
  // #ifdef H5
  if (typeof requestAnimationFrame === 'function') {
    const id = requestAnimationFrame(cb)
    return () => cancelAnimationFrame(id)
  }
  // #endif
  const id = setTimeout(cb, 16)
  return () => clearTimeout(id)
}

export class Typewriter {
  private buffer = ''
  private cancel: (() => void) | null = null
  private done = false

  constructor(private commit: CommitFn) {}

  /**
   * 把一段 text 加到待 flush 的 buffer; 若当前没排队的 frame, 调度一帧 flush.
   *
   * ``done`` 后再 push 仍会立即 commit (绕过帧节流) — 用于罕见的"drain 后又收到
   * 一条延迟 delta" 边界, 简单地直接落字保证不丢帧.
   */
  push(text: string): void {
    if (!text) return
    if (this.done) {
      this.commit(text)
      return
    }
    this.buffer += text
    if (this.cancel) return
    this.cancel = scheduleFrame(() => {
      this.cancel = null
      this._flush()
    })
  }

  private _flush(): void {
    if (!this.buffer) return
    const text = this.buffer
    this.buffer = ''
    this.commit(text)
  }

  /**
   * 流结束 (正常 / error / cancel) 调一次, 强制把 buffer 落地.
   * 之后的 ``push`` 会绕过帧节流直接 commit.
   */
  drain(): void {
    if (this.cancel) {
      this.cancel()
      this.cancel = null
    }
    this._flush()
    this.done = true
  }

  /** 仅供单测; 业务侧不应用 */
  get _bufferLength(): number {
    return this.buffer.length
  }
}
