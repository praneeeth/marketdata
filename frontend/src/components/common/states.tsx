import type { ReactNode } from 'react'
import { AlertTriangle, Inbox, Loader2, RotateCw } from 'lucide-react'
import { cn } from '@/lib/utils'

/** Placeholder while a screen or section loads. `rows` draws skeleton lines instead of a spinner. */
export function LoadingState({
  label = 'Loading…',
  rows,
  className,
}: {
  label?: string
  rows?: number
  className?: string
}) {
  if (rows && rows > 0) {
    return (
      <div role="status" aria-live="polite" aria-label={label} className={cn('space-y-3 p-4', className)}>
        {Array.from({ length: rows }).map((_, i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="h-3.5 w-1/3 animate-pulse rounded bg-muted" />
            <div className="h-3.5 flex-1 animate-pulse rounded bg-muted/70" />
          </div>
        ))}
        <span className="sr-only">{label}</span>
      </div>
    )
  }
  return (
    <div
      role="status"
      aria-live="polite"
      className={cn('flex items-center justify-center gap-2 py-10 text-[13px] text-muted-foreground', className)}
    >
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      {label}
    </div>
  )
}

/** Nothing to show yet, with an optional next step. */
export function EmptyState({
  title,
  description,
  icon,
  action,
  className,
}: {
  title: string
  description?: ReactNode
  icon?: ReactNode
  action?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('flex flex-col items-center px-6 py-10 text-center', className)}>
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent text-muted-foreground" aria-hidden="true">
        {icon ?? <Inbox className="h-5 w-5" />}
      </div>
      <p className="font-display text-[16px] font-semibold text-foreground">{title}</p>
      {description && <p className="mt-1 max-w-sm text-[13px] leading-relaxed text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  )
}

/** Something failed; say what, and offer a retry when there is one. */
export function ErrorState({
  title = "Couldn't load this",
  message,
  onRetry,
  className,
}: {
  title?: string
  message?: string
  onRetry?: () => void
  className?: string
}) {
  return (
    <div role="alert" className={cn('flex flex-col items-center px-6 py-10 text-center', className)}>
      <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-destructive/10 text-destructive" aria-hidden="true">
        <AlertTriangle className="h-5 w-5" />
      </div>
      <p className="font-display text-[16px] font-semibold text-foreground">{title}</p>
      {message && <p className="mt-1 max-w-sm break-words text-[13px] leading-relaxed text-muted-foreground">{message}</p>}
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="mt-4 inline-flex h-9 items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-[13px] font-medium text-foreground hover:bg-accent"
        >
          <RotateCw className="h-3.5 w-3.5" aria-hidden="true" />
          Try again
        </button>
      )}
    </div>
  )
}

/** Turn an unknown error into a short, readable message. */
export function errorMessage(e: unknown, fallback = 'Something went wrong. Please try again.'): string {
  if (e instanceof Error && e.message) return e.message
  if (typeof e === 'string' && e) return e
  return fallback
}
