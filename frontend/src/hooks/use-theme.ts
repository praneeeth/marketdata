import { useState, useEffect } from 'react'

/** The theme mode the user chose (system = follow the OS). */
export type ThemeMode = 'light' | 'dark' | 'system'
/** The theme actually in effect (after resolving system). */
export type Theme = 'light' | 'dark'

const STORAGE_KEY = 'candlewise-theme'

function readMode(): ThemeMode {
  const stored = localStorage.getItem(STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') return stored
  return 'system'
}

export function useTheme() {
  const [mode, setMode] = useState<ThemeMode>(readMode)
  const [systemDark, setSystemDark] = useState(
    () => window.matchMedia('(prefers-color-scheme: dark)').matches,
  )

  // Follow the OS: listen for OS theme changes and reflect them live
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  // Effective theme: explicit light/dark as is; system follows the current OS
  const theme: Theme = mode === 'system' ? (systemDark ? 'dark' : 'light') : mode

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(theme)
    localStorage.setItem(STORAGE_KEY, mode)
  }, [theme, mode])

  // For old callers: toggle between light and dark (stores the mode as explicit light/dark)
  const toggleTheme = () => setMode(theme === 'dark' ? 'light' : 'dark')

  return { theme, mode, setMode, toggleTheme }
}
