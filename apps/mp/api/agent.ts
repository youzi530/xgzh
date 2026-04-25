import { streamSSE, type SSEEvent } from '@/utils/sse'

export interface DiagnoseRequest {
  code: string
  name?: string
  question?: string
}

export interface DiagnoseHandlers {
  onStart?: (meta: { code: string; name: string; found_in_source: boolean }) => void
  onDelta: (text: string) => void
  onEnd?: () => void
  onError?: (err: Error) => void
}

export async function diagnoseStream(
  body: DiagnoseRequest,
  handlers: DiagnoseHandlers,
): Promise<void> {
  await streamSSE<DiagnoseRequest>({
    url: '/api/v1/agent/diagnose',
    method: 'POST',
    body,
    onEvent: (evt: SSEEvent) => {
      try {
        const payload = JSON.parse(evt.data)
        if (evt.event === 'start') handlers.onStart?.(payload)
        else if (evt.event === 'delta') handlers.onDelta(payload.content ?? '')
        else if (evt.event === 'end') handlers.onEnd?.()
        else if (evt.event === 'error') {
          handlers.onError?.(new Error(payload.message ?? 'agent error'))
        }
      } catch (e) {
        handlers.onError?.(e as Error)
      }
    },
    onError: handlers.onError,
    onComplete: handlers.onEnd,
  })
}
