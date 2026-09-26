import { afterEach, describe, expect, it } from 'vitest'
import { migrateLegacyStorage } from './brand'

afterEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

describe('migrateLegacyStorage', () => {
  it('moves panwatch keys to candlewise keys in both stores', () => {
    localStorage.setItem('panwatch-theme', 'dark')
    localStorage.setItem('panwatch_stocks_viewTab', '"watchlist"')
    sessionStorage.setItem('panwatch:assistant-task:1', '42')
    localStorage.setItem('unrelated', 'x')

    migrateLegacyStorage()

    expect(localStorage.getItem('candlewise-theme')).toBe('dark')
    expect(localStorage.getItem('candlewise_stocks_viewTab')).toBe('"watchlist"')
    expect(sessionStorage.getItem('candlewise:assistant-task:1')).toBe('42')
    expect(localStorage.getItem('panwatch-theme')).toBeNull()
    expect(sessionStorage.getItem('panwatch:assistant-task:1')).toBeNull()
    expect(localStorage.getItem('unrelated')).toBe('x')
  })

  it('keeps a value already stored under the new key', () => {
    localStorage.setItem('panwatch-theme', 'dark')
    localStorage.setItem('candlewise-theme', 'light')

    migrateLegacyStorage()

    expect(localStorage.getItem('candlewise-theme')).toBe('light')
    expect(localStorage.getItem('panwatch-theme')).toBeNull()
  })
})
