// SSE client plumbing on fetch + ReadableStream (the native EventSource can't send an Authorization header)
// - readSSE: one connection, resolves when the stream ends; rejects on connection failure (the caller then falls back to polling/non-streaming)
// - subscribeSSE: auto-reconnecting subscription (resumes with Last-Event-ID), for GET streams such as progress/logs
import { getToken } from './client'

export interface SSEEvent {
  /** Event sequence number (increasing on the server; used to resume) */
  id: number
  event: string
  /** The data line after JSON.parse; the raw string if parsing fails */
  data: any
}

export interface ReadSSEOptions {
  method?: 'GET' | 'POST'
  body?: unknown
  signal?: AbortSignal
  /** Sent on reconnect; the server resumes after it */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
}

/** Parse one SSE wire text block (without the trailing blank-line separator) */
function parseEventBlock(block: string): SSEEvent | null {
  const lines = block.split('\n')
  if (lines.every((l) => !l || l.startsWith(':'))) return null // heartbeat comment
  let id = 0
  let event = 'message'
  const dataLines: string[] = []
  for (const line of lines) {
    if (line.startsWith('id: ')) id = parseInt(line.slice(4), 10) || 0
    else if (line.startsWith('event: ')) event = line.slice(7)
    else if (line.startsWith('data: ')) dataLines.push(line.slice(6))
    else if (line === 'data:') dataLines.push('')
  }
  const raw = dataLines.join('\n')
  let data: any = raw
  if (raw) {
    try {
      data = JSON.parse(raw)
    } catch {
      /* keep the raw string */
    }
  }
  return { id, event, data }
}

/**
 * Open one SSE connection and consume it to the end.
 * Returns the last event sequence number received (so the caller can resume).
 * Throws on connection failure (non-2xx HTTP / wrong content-type / network error).
 */
export async function readSSE(path: string, options: ReadSSEOptions): Promise<{ lastEventId: number }> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  const token = getToken()
  if (token) headers['Authorization'] = `Bearer ${token}`
  if (options.lastEventId && options.lastEventId > 0) {
    headers['Last-Event-ID'] = String(options.lastEventId)
  }
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const res = await fetch(`/api${path}`, {
    method: options.method || 'GET',
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
    signal: options.signal,
  })
  if (!res.ok) throw new Error(`SSE HTTP ${res.status}`)
  const contentType = res.headers.get('content-type') || ''
  if (!contentType.includes('text/event-stream')) throw new Error(`Not an SSE response: ${contentType}`)
  if (!res.body) throw new Error('SSE response has no body')

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let lastEventId = options.lastEventId || 0

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    // Events are separated by blank lines
    let sepIndex: number
    while ((sepIndex = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, sepIndex)
      buffer = buffer.slice(sepIndex + 2)
      const ev = parseEventBlock(block)
      if (ev) {
        if (ev.id > 0) lastEventId = ev.id
        options.onEvent(ev)
      }
    }
  }
  return { lastEventId }
}

export interface SubscribeSSEOptions {
  /** Where the first connection resumes from (e.g. the highest known log id) */
  lastEventId?: number
  onEvent: (ev: SSEEvent) => void
  /** Called before each (re)connection succeeds; useful for UI state */
  onRetry?: (attempt: number) => void
  /** Called once retries are exhausted (the caller falls back to polling) */
  onFailed?: (err: unknown) => void
  /** Called when the server closes the stream normally (e.g. a timeout; the caller may resubscribe or fall back) */
  onClosed?: () => void
  maxRetries?: number
}

/**
 * Auto-reconnecting SSE subscription (GET). Reconnects with exponential backoff and resumes with Last-Event-ID.
 * Returns a cancel function; stops when the server closes normally (the caller closes after the done event) or retries run out.
 */
export function subscribeSSE(path: string, options: SubscribeSSEOptions): () => void {
  const controller = new AbortController()
  let closed = false
  let lastEventId = options.lastEventId || 0
  const maxRetries = options.maxRetries ?? 5

  const loop = async () => {
    let attempt = 0
    while (!closed) {
      try {
        const { lastEventId: newId } = await readSSE(path, {
          signal: controller.signal,
          lastEventId,
          onEvent: (ev) => {
            if (ev.id > 0) lastEventId = ev.id
            attempt = 0 // receiving data resets the retry count
            options.onEvent(ev)
          },
        })
        lastEventId = newId
        // The server closed the stream normally (e.g. a timeout done): the caller decides whether to resubscribe; exit here
        if (!closed) options.onClosed?.()
        return
      } catch (err) {
        if (closed || controller.signal.aborted) return
        attempt += 1
        if (attempt > maxRetries) {
          options.onFailed?.(err)
          return
        }
        options.onRetry?.(attempt)
        // Exponential backoff: 1s/2s/4s/8s/8s...
        const delay = Math.min(1000 * 2 ** (attempt - 1), 8000)
        await new Promise((r) => setTimeout(r, delay))
      }
    }
  }
  void loop()

  return () => {
    closed = true
    controller.abort()
  }
}
