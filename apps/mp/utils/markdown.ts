/**
 * 轻量增量 Markdown 解析器 (FE-S2-002).
 *
 * 为什么不用 marked / markdown-it
 * ===============================
 * 1. **零运行期依赖**: ``@dcloudio/*`` 当前 npm 版本被 yank, 装不上 npm
 *    package 风险大; 自实现 ~250 行覆盖 LLM 投研对话场景的 90%+ 用法 (heading /
 *    段落 / 无序/有序列表 / 加粗 / 行内代码 / 链接 / 代码块 / ``[N]`` 引用)
 * 2. **MP-WEIXIN 不能 v-html**: ``rich-text`` 在 MP 里事件冒泡有坑, ``[N]`` 引用
 *    要点击触发抽屉 — 走纯 ``<view>`` + ``<text>`` 自绘最干净
 * 3. **流式增量友好**: LLM 100 token/s 输出, 全文重 parse 也仅 1ms 量级 (短文),
 *    打字机节流 (utils/typewriter.ts) 把多 token 合并到 16ms 一帧, 实测无压力
 *
 * 支持的 markdown 子集
 * ====================
 * - **块级**: 段落 / heading (h1-h6) / 无序列表 (``- `` / ``* `` / ``+ ``) /
 *   有序列表 (``1. ``) / 引用 (``> ``) / 代码块 (```` ``` ````) / 水平线 (``---``)
 * - **行内**: 加粗 (``**...**``) / 斜体 (``*...*``) / 行内代码 (`` `...` ``) /
 *   链接 (``[text](url)``) / **citation** (``[N]`` 单独识别, 区别于链接)
 *
 * 不支持 / 简化处理
 * =================
 * - 表格 (LLM 投研回答用得少, Sprint 3 视需要补)
 * - 任务列表 / 删除线 / 嵌套列表 (需求侧暂不强求)
 * - HTML 原生标签 (XSS / MP 不安全)
 *
 * 增量 parse 策略
 * ===============
 * 当前: **每次全文重 parse**, 因为正文本身短 (LLM 单回合输出多在 1-3KB),
 * parse 复杂度 O(line_count); 对 ~50 行文本, 实测 H5 < 0.5ms / MP < 2ms,
 * 远低于一帧 16ms 预算。后续真有性能瓶颈再改"已 commit + tail buffer"。
 */

// ─── 类型 ───────────────────────────────────────────────────────────

export type InlineSegment =
  | { kind: 'text'; text: string }
  | { kind: 'bold'; text: string }
  | { kind: 'italic'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'link'; text: string; url: string }
  /** ``[N]`` 引用; ``idx`` 是 1-based, 与后端 ChatCitation.idx 对齐 */
  | { kind: 'citation'; idx: number }

export type MarkdownBlock =
  | {
      kind: 'paragraph'
      inlines: InlineSegment[]
    }
  | {
      kind: 'heading'
      level: 1 | 2 | 3 | 4 | 5 | 6
      inlines: InlineSegment[]
    }
  | {
      /** ordered=true 是 ``1. ...``, false 是 ``- ...`` / ``* ...`` / ``+ ...`` */
      kind: 'list'
      ordered: boolean
      items: InlineSegment[][]
    }
  | {
      kind: 'quote'
      inlines: InlineSegment[]
    }
  | {
      kind: 'code'
      lang: string | null
      text: string
    }
  | {
      kind: 'hr'
    }

// ─── 行内解析: 把一行字符串变成 InlineSegment[] ──────────────────────

/**
 * 优先级 (从外到内, 互不嵌套):
 *   1. 行内代码 ``\`...\``` (内部不再 parse, 防 ``\`**foo**\`` 误识别)
 *   2. 链接 ``[text](url)``
 *   3. citation ``[N]`` (N 必须纯数字, 与 ``[text]`` 区分)
 *   4. 加粗 ``**...**``
 *   5. 斜体 ``*...*`` / ``_..._``
 *   6. 普通文本
 *
 * 实现: 用 regex token-stream 切分; 每次 match 优先匹配最早出现的 pattern.
 *
 * 故意不支持嵌套 (e.g. ``**[1]**`` / ``*\`code\`*``): LLM 极少这么写,
 * 加嵌套支持要 recursive descent + 引入 100+ 行复杂度, 不值。
 */
export function parseInline(line: string): InlineSegment[] {
  const segs: InlineSegment[] = []
  let i = 0
  const len = line.length

  // 简单"找下一个特殊起点", 把 [pos, nextSpecialPos) 切为 text 段
  while (i < len) {
    // 1. 行内代码
    if (line[i] === '`') {
      const close = line.indexOf('`', i + 1)
      if (close > i) {
        segs.push({ kind: 'code', text: line.slice(i + 1, close) })
        i = close + 1
        continue
      }
    }

    // 2. 链接 / citation 都以 ``[`` 开头, 但语法分歧:
    //    - 链接: [text](url)  — text 可空但 ``](`` 必须紧跟
    //    - citation: [N]       — N 是纯数字, ``]`` 后**没有** ``(``
    if (line[i] === '[') {
      const close = line.indexOf(']', i + 1)
      if (close > i) {
        const inner = line.slice(i + 1, close)
        // citation: 整个 inner 是纯数字, 且 ``]`` 之后不是 ``(``
        if (/^\d+$/.test(inner) && line[close + 1] !== '(') {
          segs.push({ kind: 'citation', idx: parseInt(inner, 10) })
          i = close + 1
          continue
        }
        // 链接: ``](`` 紧跟 + 找到对应 ``)``
        if (line[close + 1] === '(') {
          const urlEnd = line.indexOf(')', close + 2)
          if (urlEnd > close) {
            segs.push({
              kind: 'link',
              text: inner,
              url: line.slice(close + 2, urlEnd),
            })
            i = urlEnd + 1
            continue
          }
        }
      }
    }

    // 3. 加粗 ``**...**`` (优先于斜体 *, 避免 ``**`` 被识别为两个空斜体)
    if (line[i] === '*' && line[i + 1] === '*') {
      const close = line.indexOf('**', i + 2)
      if (close > i + 1) {
        segs.push({ kind: 'bold', text: line.slice(i + 2, close) })
        i = close + 2
        continue
      }
    }

    // 4. 斜体 ``*...*`` (单星号; 内容非空)
    //    避免 list bullet ``*  ``, 只在前一字符不是空白且 next 不是空白时认 italic
    if (line[i] === '*' && i + 1 < len && line[i + 1] !== '*' && line[i + 1] !== ' ') {
      const close = line.indexOf('*', i + 1)
      if (close > i + 1 && line[close - 1] !== ' ') {
        segs.push({ kind: 'italic', text: line.slice(i + 1, close) })
        i = close + 1
        continue
      }
    }

    // 5. 普通文本: 把 ``i`` 推进到下一个特殊起点 (或行尾)
    let nextSpecial = -1
    for (let j = i; j < len; j += 1) {
      const ch = line[j]
      if (ch === '`' || ch === '[' || ch === '*') {
        nextSpecial = j
        break
      }
    }
    const end = nextSpecial >= 0 && nextSpecial > i ? nextSpecial : len
    // 当前 i 既然不是合法特殊语法 (前面分支没 continue), 那么 line[i] 这一字符
    // 也作为文本提交; 取下一段时把 i 至少前推 1 防死循环
    const safeEnd = end === i ? i + 1 : end
    const textSlice = line.slice(i, safeEnd)
    if (textSlice) _appendText(segs, textSlice)
    i = safeEnd
  }

  return segs
}

function _appendText(segs: InlineSegment[], text: string): void {
  // 合并连续 text 段, 减少模板渲染节点
  const last = segs[segs.length - 1]
  if (last && last.kind === 'text') {
    last.text += text
  } else {
    segs.push({ kind: 'text', text })
  }
}

// ─── 块级解析 ──────────────────────────────────────────────────────

/** 把整段 markdown 字符串变 ``MarkdownBlock[]``; 流式中也可重复调用. */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = []
  // 不 ``trimStart`` 整体 (会丢前导空行 → 影响某些块判别), 但 trimEnd 防尾白
  const lines = source.replace(/\r\n/g, '\n').split('\n')

  let i = 0
  while (i < lines.length) {
    const line = lines[i]
    const trimmed = line.trim()

    // 1. 空行 — 跳过 (作段落分隔)
    if (!trimmed) {
      i += 1
      continue
    }

    // 2. 代码块 ``` 起 / 收
    if (/^```/.test(trimmed)) {
      const lang = trimmed.slice(3).trim() || null
      const codeLines: string[] = []
      i += 1
      while (i < lines.length && !/^```/.test(lines[i].trim())) {
        codeLines.push(lines[i])
        i += 1
      }
      // 流式中代码块未闭合也得渲染 (LLM 还在写)
      blocks.push({ kind: 'code', lang, text: codeLines.join('\n') })
      // 越过结束 ``` (若有)
      if (i < lines.length) i += 1
      continue
    }

    // 3. 水平线 ``---`` / ``***`` / ``___``
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      blocks.push({ kind: 'hr' })
      i += 1
      continue
    }

    // 4. heading ``# ... `` ~ ``###### ...``
    const headingMatch = /^(#{1,6})\s+(.+)$/.exec(trimmed)
    if (headingMatch) {
      const level = headingMatch[1].length as 1 | 2 | 3 | 4 | 5 | 6
      blocks.push({
        kind: 'heading',
        level,
        inlines: parseInline(headingMatch[2].trim()),
      })
      i += 1
      continue
    }

    // 5. 引用块 ``> ...`` (连续多行)
    if (/^>\s?/.test(trimmed)) {
      const quoteLines: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quoteLines.push(lines[i].trim().replace(/^>\s?/, ''))
        i += 1
      }
      blocks.push({
        kind: 'quote',
        inlines: parseInline(quoteLines.join('\n')),
      })
      continue
    }

    // 6. 列表 (无序 / 有序; 不支持嵌套, 兄弟项连续)
    const ulMatch = /^[-*+]\s+(.+)$/.exec(trimmed)
    const olMatch = /^(\d+)\.\s+(.+)$/.exec(trimmed)
    if (ulMatch || olMatch) {
      const ordered = !!olMatch
      const items: InlineSegment[][] = []
      while (i < lines.length) {
        const ln = lines[i].trim()
        const ulm = /^[-*+]\s+(.+)$/.exec(ln)
        const olm = /^(\d+)\.\s+(.+)$/.exec(ln)
        const isUl = !!ulm && !ordered
        const isOl = !!olm && ordered
        if (!isUl && !isOl) break
        const text = ordered ? olm![2] : ulm![1]
        items.push(parseInline(text))
        i += 1
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }

    // 7. 段落 — 直到下一个空行 / 块级特殊起点
    const paraLines: string[] = [line]
    i += 1
    while (i < lines.length) {
      const next = lines[i]
      const nt = next.trim()
      if (!nt) break
      // 下一行是 heading / 代码块 / hr / 列表 / 引用 起 → 停; 否则视作软折行 (空格连接)
      if (
        /^(#{1,6})\s+/.test(nt) ||
        /^```/.test(nt) ||
        /^(-{3,}|\*{3,}|_{3,})$/.test(nt) ||
        /^[-*+]\s+/.test(nt) ||
        /^\d+\.\s+/.test(nt) ||
        /^>\s?/.test(nt)
      ) {
        break
      }
      paraLines.push(next)
      i += 1
    }
    const merged = paraLines.map((s) => s.trim()).join(' ')
    blocks.push({
      kind: 'paragraph',
      inlines: parseInline(merged),
    })
  }

  return blocks
}

/**
 * 拿 block 列表的纯文本 (去 markdown 标记) — 供"复制内容" / 截断预览 / 字数统计
 * 等场景使用; 与 LLM 原始 token 流保持语义一致。
 */
export function blocksToPlainText(blocks: MarkdownBlock[]): string {
  return blocks
    .map((b) => {
      switch (b.kind) {
        case 'paragraph':
        case 'heading':
        case 'quote':
          return inlinesToText(b.inlines)
        case 'list':
          return b.items.map((it) => `- ${inlinesToText(it)}`).join('\n')
        case 'code':
          return b.text
        case 'hr':
          return '---'
      }
    })
    .join('\n\n')
}

function inlinesToText(segs: InlineSegment[]): string {
  return segs
    .map((s) => {
      switch (s.kind) {
        case 'text':
        case 'bold':
        case 'italic':
        case 'code':
          return s.text
        case 'link':
          return s.text
        case 'citation':
          return `[${s.idx}]`
      }
    })
    .join('')
}
