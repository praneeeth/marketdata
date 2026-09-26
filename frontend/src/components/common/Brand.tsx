import type { ReactNode } from 'react'
import { FlaskConical, Info } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useCompliance } from '@/hooks/use-compliance'

/**
 * The research disclaimer as a visible note. Used on every research summary and in
 * onboarding; the footer uses the compact variant. Never collapsed or hidden.
 */
export function DisclaimerNote({
  compact = false,
  className,
  children,
}: {
  compact?: boolean
  className?: string
  children?: ReactNode
}) {
  const { shortDisclaimer } = useCompliance()
  return (
    <aside
      aria-label="Disclaimer"
      data-testid="research-disclaimer"
      className={cn(
        'note flex gap-2',
        compact ? 'px-3 py-2 text-[11px] leading-relaxed' : 'px-3.5 py-3 text-[12px] leading-relaxed',
        className,
      )}
    >
      <Info className={cn('mt-0.5 shrink-0', compact ? 'h-3 w-3' : 'h-3.5 w-3.5')} aria-hidden="true" />
      <span>{children ?? shortDisclaimer}</span>
    </aside>
  )
}

/** Marks anything from paper trading: never real money, never real orders. */
export function SimulationBadge({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-md border border-simulation/30 bg-simulation/10 px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-simulation',
        className,
      )}
    >
      <FlaskConical className="h-3 w-3" aria-hidden="true" />
      Simulation
    </span>
  )
}

/** Consistent page heading: optional eyebrow, serif title, one-line description and actions. */
export function PageHeader({
  title,
  eyebrow,
  description,
  actions,
  badge,
  className,
}: {
  title: ReactNode
  eyebrow?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  badge?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('mb-5 flex flex-col gap-3 md:mb-6 md:flex-row md:items-end md:justify-between', className)}>
      <div className="min-w-0">
        {eyebrow && <div className="eyebrow mb-1">{eyebrow}</div>}
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="page-title">{title}</h1>
          {badge}
        </div>
        {description && <p className="mt-1 text-[13px] text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}
