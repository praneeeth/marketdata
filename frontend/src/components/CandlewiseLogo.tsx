import { cn } from '@/lib/utils'
import { PRODUCT_NAME } from '@/lib/brand'

/**
 * The Candlewise mark: one candlestick whose upper wick ends in a small flame.
 * The body uses the current text colour, so it follows light/dark themes; it is never
 * green or red, so it doesn't suggest a price direction.
 */
export function CandlewiseMark({ className, title = PRODUCT_NAME }: { className?: string; title?: string }) {
  return (
    <svg
      viewBox="0 0 32 32"
      className={className}
      {...(title ? { role: 'img', 'aria-label': title } : { 'aria-hidden': true })}
    >
      {title && <title>{title}</title>}
      <line x1="16" y1="24" x2="16" y2="28.5" stroke="currentColor" strokeOpacity="0.7" strokeWidth="1.4" strokeLinecap="round" />
      <rect x="11" y="13" width="10" height="11" rx="1.6" fill="currentColor" />
      <line x1="16" y1="13" x2="16" y2="10.4" stroke="currentColor" strokeOpacity="0.7" strokeWidth="1.4" strokeLinecap="round" />
      <path d="M16 2.6 C17.9 4.9 18.6 6.4 18.6 7.8 A2.6 2.6 0 0 1 13.4 7.8 C13.4 6.4 14.1 4.9 16 2.6 Z" fill="#f59e0b" />
      <path d="M16 5.6 C16.8 6.6 17.1 7.3 17.1 7.9 A1.1 1.1 0 0 1 14.9 7.9 C14.9 7.3 15.2 6.6 16 5.6 Z" fill="#fde68a" />
    </svg>
  )
}

/** Mark plus the "Candlewise" wordmark. */
export function CandlewiseLogo({
  className,
  markClassName = 'w-7 h-7',
  wordmarkClassName = 'text-[15px] font-bold',
}: {
  className?: string
  markClassName?: string
  wordmarkClassName?: string
}) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-foreground', className)}>
      <CandlewiseMark className={cn('shrink-0 text-slate-600 dark:text-slate-300', markClassName)} title="" />
      <span className={wordmarkClassName}>{PRODUCT_NAME}</span>
    </span>
  )
}
