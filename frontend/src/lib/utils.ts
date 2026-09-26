import { useState, useEffect } from 'react'
export { cn } from '@candlewise/base-ui'
import { DASH, formatIST } from './format'

/**
 * useState persisted to localStorage
 * @param key localStorage key
 * @param defaultValue default value
 */
export function useLocalStorage<T>(key: string, defaultValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      const saved = localStorage.getItem(key)
      if (saved !== null) {
        return JSON.parse(saved)
      }
    } catch {
      // ignore
    }
    return defaultValue
  })

  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {
      // ignore
    }
  }, [key, value])

  return [value, setValue]
}

// ==================== Time formatting helpers (IST) ====================

/** Time only, in IST: "15:30"; empty for missing input. */
export function formatTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  const out = formatIST(isoTime, 'time')
  return out === DASH ? '' : out
}

/** Date and time, in IST: "26 Sept, 15:30"; empty for missing input. */
export function formatDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  const out = formatIST(isoTime, 'short')
  return out === DASH ? '' : out
}

/** Full date and time with seconds, in IST; empty for missing input. */
export function formatFullDateTime(isoTime?: string | null): string {
  if (!isoTime) return ''
  const out = formatIST(isoTime, 'full')
  return out === DASH ? '' : out
}
