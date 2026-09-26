import { fetchAPI } from './client'

export interface SelfCheckItem {
  category: 'datasource' | 'ai' | 'notify'
  key: string
  name: string
  status: 'ok' | 'slow' | 'fail'
  latency_ms: number
  error: string | null
  /** Fix hint (only set for fail). */
  hint: string
  /** e.g. for notifications "config validated only, nothing sent". */
  note: string | null
  /** Second-level group (for AI = the provider name); null for other categories. */
  group: string | null
}

export interface SelfCheckResult {
  items: SelfCheckItem[]
  summary: {
    total: number
    ok: number
    slow: number
    fail: number
  }
  notify_send: boolean
}

export const healthApi = {
  /** System self-check (data source/AI/notification connectivity). notifySend=true really sends a test notification. */
  selfcheck: (notifySend = false) =>
    fetchAPI<SelfCheckResult>('/health/selfcheck?notify_send=' + notifySend, { timeoutMs: 60000 }),

  /** Only the list of items to check (no probing; returns instantly), to render first and then check each. */
  selfcheckList: () =>
    fetchAPI<{ items: Array<{ category: string; key: string; name: string; group: string | null }> }>(
      '/health/selfcheck?list=1',
    ),

  /** Probe only the given keys (for item-by-item / low-concurrency self-checks). notifySend only affects the notify category. */
  selfcheckKeys: (keys: string[], notifySend = false) =>
    fetchAPI<SelfCheckResult>(
      '/health/selfcheck?keys=' +
        encodeURIComponent(keys.join(',')) +
        '&notify_send=' +
        notifySend,
      { timeoutMs: 30000 },
    ),
}
