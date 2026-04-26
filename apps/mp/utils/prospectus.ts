/**
 * 跨端打开招股书 PDF (FE-S2-003).
 *
 * 平台分支
 * ========
 * - **H5**: ``window.open(url, '_blank')`` 直接外跳浏览器 / 新标签
 * - **MP-WEIXIN**:
 *   1. ``wx.downloadFile`` 拉到本地临时路径 (有效期, ``ResponseHeader`` 走系统)
 *   2. ``wx.openDocument`` 用系统组件打开 (PDF / Word / Excel 都支持)
 *   3. 任何一步失败 → fallback 复制 URL 到剪贴板 + toast 提示在 PC 浏览器打开
 *   ⚠️ 微信小程序后台需提前在 ``downloadFile`` 业务域名白名单加入 hkexnews 等域名,
 *      否则 ``downloadFile`` 直接 ``fail``. Sprint 3 上线前由运营在小程序后台配.
 * - **APP** (uni-app App 端): ``plus.runtime.openURL(url)`` 调系统浏览器
 *
 * 设计取舍
 * ========
 * - **不下载到永久存储**: 法务侧"招股书属第三方版权材料, 不应缓存到客户端持久化"
 *   (spec/06), MP 走临时路径 + 系统组件预览正合规.
 * - **静默 fallback**: 任何一步失败都回退到"复制 URL"而非"显眼报错", 避免阻塞 —
 *   用户看到的是"链接已复制, 请在浏览器打开"toast, 而不是红色弹窗吓人.
 * - **不弹"加载中" loading**: ``downloadFile`` 自带进度回调但 PDF 通常 < 10MB,
 *   带 4G/Wi-Fi 走完通常 < 2s; 加 loading 反而割裂. MP / App 在 ``downloadFile`` /
 *   ``openURL`` 之间会有 0.5~2s 空窗, 用户可见 "加载中" 是系统弹窗自带的.
 *
 * 使用
 * ====
 * ```ts
 * import { openProspectusUrl } from '@/utils/prospectus'
 * await openProspectusUrl('https://...../招股书.pdf', '美团-W')
 * ```
 */

/** 临时文件 mime 推断: 仅靠 URL 后缀, 不可靠但够用 (招股书 99% 是 PDF) */
function guessFileExt(url: string): string {
  const u = url.toLowerCase()
  if (u.endsWith('.pdf')) return 'pdf'
  if (u.endsWith('.doc')) return 'doc'
  if (u.endsWith('.docx')) return 'docx'
  if (u.endsWith('.xls')) return 'xls'
  if (u.endsWith('.xlsx')) return 'xlsx'
  // 招股书通常是 PDF; URL 没扩展名时默认按 PDF 试
  return 'pdf'
}

/**
 * URL 兜底 — 复制到剪贴板 + toast 提示用户去浏览器打开.
 *
 * 这是所有打开失败场景的最后一道兜底, 永远不会再失败 (uni.setClipboardData
 * 在所有平台都同步 / 异步可用).
 */
function fallbackCopyUrl(url: string, hint: string = '请在浏览器中打开'): void {
  uni.setClipboardData({
    data: url,
    showToast: false,
    success: () => {
      uni.showToast({ title: `链接已复制, ${hint}`, icon: 'none', duration: 2400 })
    },
    fail: () => {
      uni.showToast({ title: '链接复制失败', icon: 'none' })
    },
  })
}

/** H5: 直接 window.open; 绝大多数桌面浏览器会新标签预览 PDF */
function openOnH5(url: string): void {
  // #ifdef H5
  try {
    // _blank 让浏览器自己决定预览还是下载; noopener 防止子页 window.opener
    window.open(url, '_blank', 'noopener,noreferrer')
  } catch {
    fallbackCopyUrl(url)
  }
  // #endif
}

/** MP-WEIXIN: downloadFile + openDocument; 任何一步失败 → fallback 复制 URL */
function openOnMpWeixin(url: string): void {
  // #ifdef MP-WEIXIN
  uni.showLoading({ title: '加载中…', mask: true })
  uni.downloadFile({
    url,
    success: (res) => {
      uni.hideLoading()
      // statusCode != 200 也走 success (uni 行为不一致), 显式判一下
      if (res.statusCode !== 200) {
        fallbackCopyUrl(url, '后端拒绝下载, 请在浏览器打开')
        return
      }
      const fileType = guessFileExt(url)
      uni.openDocument({
        filePath: res.tempFilePath,
        fileType: fileType as 'pdf' | 'doc' | 'docx' | 'xls' | 'xlsx',
        showMenu: true,
        fail: () => {
          fallbackCopyUrl(url, '系统不支持预览, 请在浏览器打开')
        },
      })
    },
    fail: (err) => {
      uni.hideLoading()
      // 业务域名未加白时 errMsg 形如 "downloadFile:fail url not in domain list"
      const msg = String(err?.errMsg ?? '')
      const hint = /domain list/i.test(msg)
        ? '请在浏览器打开'
        : '下载失败, 请在浏览器打开'
      fallbackCopyUrl(url, hint)
    },
  })
  // #endif
}

/** App (uni-app App 端): 调系统浏览器打开 */
function openOnApp(url: string): void {
  // #ifdef APP-PLUS
  try {
    // plus 在 App 环境下注入; TS 不识别走 declare-on-the-fly
    const plus = (globalThis as unknown as { plus?: { runtime: { openURL: (u: string) => void } } }).plus
    if (plus?.runtime?.openURL) {
      plus.runtime.openURL(url)
    } else {
      fallbackCopyUrl(url)
    }
  } catch {
    fallbackCopyUrl(url)
  }
  // #endif
}

/**
 * 跨端打开 PDF URL 入口.
 *
 * @param url      招股书 PDF 完整 URL (后端 ``IPODetail.prospectus_url``)
 * @param ipoName  仅日志 / toast 用; 不影响打开行为
 */
export function openProspectusUrl(url: string, ipoName: string = ''): void {
  if (!url) {
    uni.showToast({ title: '原文链接无效', icon: 'none' })
    return
  }
  // 三端各走一支; 条件编译保证打包时仅其中一支被嵌入对应平台 bundle
  // #ifdef H5
  openOnH5(url)
  // #endif
  // #ifdef MP-WEIXIN
  openOnMpWeixin(url)
  // #endif
  // #ifdef APP-PLUS
  openOnApp(url)
  // #endif
  // 其他平台 (MP-ALIPAY / MP-TOUTIAO / ...) 兜底走剪贴板; ipoName 仅给日志
  // #ifndef H5 || MP-WEIXIN || APP-PLUS
  void ipoName
  fallbackCopyUrl(url)
  // #endif
}
