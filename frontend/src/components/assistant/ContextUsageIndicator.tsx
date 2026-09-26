import { Gauge } from 'lucide-react'
import type { ContextUsage } from '@panwatch/api'

interface ContextUsageIndicatorProps {
  usage: ContextUsage | null
  onClick: () => void
}

const STATE_LABELS: Record<ContextUsage['state'], string> = {
  normal: 'Context OK',
  warning: 'Context large',
  needs_compression: 'Needs compression',
}

export function ContextUsageIndicator({ usage, onClick }: ContextUsageIndicatorProps) {
  if (!usage) return null
  const stateClass = usage.state === 'needs_compression'
    ? 'text-rose-600 dark:text-rose-400'
    : usage.state === 'warning'
      ? 'text-amber-600 dark:text-amber-400'
      : 'text-muted-foreground'

  return (
    <button
      type="button"
      onClick={onClick}
      className={`inline-flex min-w-0 items-center gap-1 rounded-md px-1.5 py-1 text-[11px] transition-colors hover:bg-accent/60 ${stateClass}`}
      aria-label="View context usage"
      title={`${STATE_LABELS[usage.state]}, estimated input tokens ${usage.total_tokens.toLocaleString()} / ${usage.budget_tokens.toLocaleString()}`}
    >
      <Gauge className="h-3.5 w-3.5 shrink-0" />
      <span className="tabular-nums">{usage.total_tokens.toLocaleString()} / {usage.budget_tokens.toLocaleString()}</span>
    </button>
  )
}
