/**
 * 请求封装：统一 baseURL / 错误处理 / 超时
 * 不要在组件中直写 uni.request，一律走这里。
 */

const DEFAULT_BASE_URL = 'http://localhost:8000'

export interface RequestOptions<TData = unknown> {
  url: string
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  data?: TData
  header?: Record<string, string>
  timeout?: number
}

export class APIError extends Error {
  constructor(
    public statusCode: number,
    message: string,
    public detail?: unknown,
  ) {
    super(message)
  }
}

function getBaseURL(): string {
  // #ifdef H5
  return ''
  // #endif
  // #ifndef H5
  return DEFAULT_BASE_URL
  // #endif
}

export async function request<TResp = unknown, TData = unknown>(
  opts: RequestOptions<TData>,
): Promise<TResp> {
  const baseURL = getBaseURL()
  const fullUrl = opts.url.startsWith('http') ? opts.url : `${baseURL}${opts.url}`

  return new Promise<TResp>((resolve, reject) => {
    uni.request({
      url: fullUrl,
      method: opts.method ?? 'GET',
      data: opts.data,
      header: {
        'Content-Type': 'application/json',
        ...opts.header,
      },
      timeout: opts.timeout ?? 15000,
      success: (res) => {
        const status = res.statusCode ?? 0
        if (status >= 200 && status < 300) {
          resolve(res.data as TResp)
        } else {
          reject(new APIError(status, `HTTP ${status}`, res.data))
        }
      },
      fail: (err) => {
        reject(new APIError(0, err.errMsg || 'network error', err))
      },
    })
  })
}
