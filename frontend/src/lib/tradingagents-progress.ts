export const TERMINAL_PROGRESS_STATUSES = ['success', 'failed', 'stale'] as const

export type TerminalProgressStatus = typeof TERMINAL_PROGRESS_STATUSES[number]

export function isTerminalProgressStatus(status: string | null | undefined): status is TerminalProgressStatus {
  return TERMINAL_PROGRESS_STATUSES.includes(status as TerminalProgressStatus)
}

/**
 * An SSE close only means this connection ended, not that the task ended.
 * not_found, running, timeout and empty states should all hand over to polling.
 */
export function shouldContinueProgressWatch(
  status: string | null | undefined,
  event: 'progress' | 'done' = 'progress',
): boolean {
  void event
  return !isTerminalProgressStatus(status)
}
