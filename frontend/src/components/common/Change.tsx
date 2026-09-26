import { cn } from '@/lib/utils'
import { direction, formatINR, formatPct, formatSigned } from '@/lib/format'

type Kind = 'pct' | 'inr' | 'number'

const ARROW = { up: '▲', down: '▼', flat: '' } as const
const SPOKEN = { up: 'up', down: 'down', flat: 'unchanged' } as const

/**
 * A price move: colour (green up / red down) is never the only cue. The value carries a
 * +/- sign, an arrow shows the direction, and screen readers hear "up" or "down".
 */
export function Change({
  value,
  kind = 'pct',
  digits = 2,
  arrow = true,
  className,
}: {
  value: number | null | undefined
  kind?: Kind
  digits?: number
  arrow?: boolean
  className?: string
}) {
  const d = direction(value)
  const text =
    kind === 'pct' ? formatPct(value, digits) : kind === 'inr' ? formatINR(value, { signed: true, digits }) : formatSigned(value, digits)
  return (
    <span
      className={cn(
        'inline-flex items-baseline gap-0.5 tabular-nums whitespace-nowrap',
        d === 'up' ? 'text-up' : d === 'down' ? 'text-down' : 'text-muted-foreground',
        className,
      )}
      data-direction={d}
    >
      {arrow && d !== 'flat' && (
        <span aria-hidden="true" className="text-[0.7em] leading-none">
          {ARROW[d]}
        </span>
      )}
      <span className="sr-only">{SPOKEN[d]} </span>
      {text}
    </span>
  )
}
