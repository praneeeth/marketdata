import { describe, expect, it } from 'vitest'
import {
  DASH,
  direction,
  directionClass,
  formatCompact,
  formatINR,
  formatIST,
  formatNumber,
  formatPct,
  formatRelative,
  formatSigned,
} from './format'

describe('formatINR', () => {
  it('uses Indian grouping below a lakh', () => {
    expect(formatINR(1234.5)).toBe('₹1,234.50')
    expect(formatINR(99999)).toBe('₹99,999.00')
  })
  it('uses lakh and crore units', () => {
    expect(formatINR(250000)).toBe('₹2.5 L')
    expect(formatINR(12345678)).toBe('₹1.23 Cr')
    expect(formatINR(-12000000)).toBe('-₹1.2 Cr')
  })
  it('can show full digits with Indian grouping', () => {
    expect(formatINR(1234567.8, { compact: false })).toBe('₹12,34,567.80')
  })
  it('signs P&L on request', () => {
    expect(formatINR(500, { signed: true })).toBe('+₹500.00')
    expect(formatINR(-500, { signed: true })).toBe('-₹500.00')
    expect(formatINR(0, { signed: true })).toBe('₹0.00')
  })
  it('returns a dash for missing values', () => {
    expect(formatINR(null)).toBe(DASH)
    expect(formatINR(Number.NaN)).toBe(DASH)
  })
})

describe('numbers and percentages', () => {
  it('formats plain and compact numbers', () => {
    expect(formatNumber(1234567.891)).toBe('12,34,567.89')
    expect(formatNumber(undefined)).toBe(DASH)
    expect(formatCompact(10240700)).toBe('1.02 Cr')
    expect(formatCompact(150000)).toBe('1.5 L')
    expect(formatCompact(-2500)).toBe('-2,500')
    expect(formatCompact(12.345)).toBe('12.35')
  })
  it('always signs percentages', () => {
    expect(formatPct(1.234)).toBe('+1.23%')
    expect(formatPct(-0.5)).toBe('-0.50%')
    expect(formatPct(0)).toBe('0.00%')
    expect(formatPct(3, 1, false)).toBe('3.0%')
    expect(formatSigned(-14.3)).toBe('-14.30')
    expect(formatSigned(null)).toBe(DASH)
  })
  it('maps direction to token classes', () => {
    expect(direction(2)).toBe('up')
    expect(direction(-2)).toBe('down')
    expect(direction(0)).toBe('flat')
    expect(directionClass(1)).toBe('text-up')
    expect(directionClass(-1)).toBe('text-down')
    expect(directionClass(null)).toBe('text-muted-foreground')
  })
})

describe('IST times', () => {
  const utc = '2026-09-26T10:00:00Z' // 15:30 IST
  it('converts to Asia/Kolkata', () => {
    expect(formatIST(utc, 'time')).toBe('15:30')
    expect(formatIST(utc, 'time', { suffix: true })).toBe('15:30 IST')
    expect(formatIST(utc, 'date')).toMatch(/26 Sept? 2026/)
    expect(formatIST(utc, 'short')).toMatch(/26 Sept?, 15:30/)
    expect(formatIST('not a date')).toBe(DASH)
  })
  it('formats relative times', () => {
    const now = new Date('2026-09-26T10:10:00Z')
    expect(formatRelative('2026-09-26T10:09:30Z', now)).toBe('just now')
    expect(formatRelative('2026-09-26T10:00:00Z', now)).toBe('10 min ago')
    expect(formatRelative('2026-09-26T07:00:00Z', now)).toBe('3 h ago')
    expect(formatRelative('2026-09-24T10:00:00Z', now)).toBe('2 d ago')
    expect(formatRelative(null, now)).toBe(DASH)
  })
})
