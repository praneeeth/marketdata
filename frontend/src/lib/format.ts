/**
 * Number, money and time formatting for Indian users: en-IN digit grouping (12,34,567),
 * rupees in lakh (L, 1e5) and crore (Cr, 1e7), and times in IST (Asia/Kolkata).
 * Every function returns an em dash for missing or non-finite input.
 */

export const IST = 'Asia/Kolkata'
export const LOCALE = 'en-IN'
export const DASH = '—'

const LAKH = 1e5
const CRORE = 1e7

function isNum(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v)
}

/** Plain number with Indian grouping: 1234567.8 -> "12,34,567.80". */
export function formatNumber(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH
  return v.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Large quantity in lakh/crore without a currency sign: 25000000 -> "2.5 Cr". */
export function formatCompact(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : ''
  const opts = { maximumFractionDigits: digits }
  if (abs >= CRORE) return `${sign}${(abs / CRORE).toLocaleString(LOCALE, opts)} Cr`
  if (abs >= LAKH) return `${sign}${(abs / LAKH).toLocaleString(LOCALE, opts)} L`
  return `${sign}${abs.toLocaleString(LOCALE, { maximumFractionDigits: abs >= 1000 ? 0 : digits })}`
}

export interface InrOptions {
  /** Show lakh/crore units for 1 lakh and above (default true). */
  compact?: boolean
  /** Decimal places (default 2). */
  digits?: number
  /** Prefix positive values with "+" (for P&L). */
  signed?: boolean
}

/** Rupees: 1234.5 -> "₹1,234.50"; 2500000 -> "₹25 L"; -12000000 -> "-₹1.2 Cr". */
export function formatINR(v: number | null | undefined, opts: InrOptions = {}): string {
  if (!isNum(v)) return DASH
  const { compact = true, digits = 2, signed = false } = opts
  const abs = Math.abs(v)
  const sign = v < 0 ? '-' : signed && v > 0 ? '+' : ''
  let body: string
  if (compact && abs >= CRORE) body = `${(abs / CRORE).toLocaleString(LOCALE, { maximumFractionDigits: 2 })} Cr`
  else if (compact && abs >= LAKH) body = `${(abs / LAKH).toLocaleString(LOCALE, { maximumFractionDigits: 2 })} L`
  else body = abs.toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })
  return `${sign}₹${body}`
}

/** Percentage with an explicit sign: 1.234 -> "+1.23%", -0.5 -> "-0.50%", 0 -> "0.00%". */
export function formatPct(v: number | null | undefined, digits = 2, signed = true): string {
  if (!isNum(v)) return DASH
  const sign = signed && v > 0 ? '+' : v < 0 ? '-' : ''
  return `${sign}${Math.abs(v).toFixed(digits)}%`
}

/** Signed plain number: 14.3 -> "+14.30". */
export function formatSigned(v: number | null | undefined, digits = 2): string {
  if (!isNum(v)) return DASH
  const sign = v > 0 ? '+' : v < 0 ? '-' : ''
  return `${sign}${Math.abs(v).toLocaleString(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits })}`
}

export type Direction = 'up' | 'down' | 'flat'

export function direction(v: number | null | undefined): Direction {
  if (!isNum(v) || v === 0) return 'flat'
  return v > 0 ? 'up' : 'down'
}

/** Tailwind text colour for a price direction (token based; never used for anything else). */
export function directionClass(v: number | null | undefined): string {
  const d = direction(v)
  return d === 'up' ? 'text-up' : d === 'down' ? 'text-down' : 'text-muted-foreground'
}

function toDate(input: string | number | Date | null | undefined): Date | null {
  if (input === null || input === undefined || input === '') return null
  const d = input instanceof Date ? input : new Date(input)
  return Number.isNaN(d.getTime()) ? null : d
}

export type TimeStyle = 'time' | 'date' | 'datetime' | 'short' | 'full'

const TIME_FORMATS: Record<TimeStyle, Intl.DateTimeFormatOptions> = {
  time: { hour: '2-digit', minute: '2-digit', hour12: false },
  date: { day: 'numeric', month: 'short', year: 'numeric' },
  short: { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false },
  datetime: { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false },
  full: {
    day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  },
}

/**
 * A time in IST: "26 Sept 2026, 15:30". Styles: time "15:30", date "26 Sept 2026",
 * short "26 Sept, 15:30", datetime, full (with seconds).
 */
export function formatIST(
  input: string | number | Date | null | undefined,
  style: TimeStyle = 'datetime',
  opts: { suffix?: boolean } = {},
): string {
  const d = toDate(input)
  if (!d) return DASH
  const text = d.toLocaleString(LOCALE, { ...TIME_FORMATS[style], timeZone: IST })
  return opts.suffix && style !== 'date' ? `${text} IST` : text
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", else the IST date. */
export function formatRelative(input: string | number | Date | null | undefined, now: Date = new Date()): string {
  const d = toDate(input)
  if (!d) return DASH
  const sec = Math.round((now.getTime() - d.getTime()) / 1000)
  if (sec < 0) return formatIST(d, 'short')
  if (sec < 60) return 'just now'
  if (sec < 3600) return `${Math.floor(sec / 60)} min ago`
  if (sec < 86400) return `${Math.floor(sec / 3600)} h ago`
  if (sec < 7 * 86400) return `${Math.floor(sec / 86400)} d ago`
  return formatIST(d, 'date')
}
