export type SuggestionAction =
  | 'buy'
  | 'add'
  | 'reduce'
  | 'sell'
  | 'hold'
  | 'watch'
  | 'alert'
  | 'avoid'

export const suggestionActionColors: Record<SuggestionAction, string> = {
  buy: 'bg-up text-white',
  add: 'bg-up text-white',
  reduce: 'bg-down text-white',
  sell: 'bg-down text-white',
  hold: 'bg-amber-500 text-white',
  watch: 'bg-slate-500 text-white',
  alert: 'bg-blue-500 text-white',
  avoid: 'bg-down text-white',
}

export const suggestionActionLabels: Record<SuggestionAction, string> = {
  buy: 'Buy',
  add: 'Add',
  reduce: 'Reduce',
  sell: 'Sell',
  hold: 'Hold',
  watch: 'Watch',
  avoid: 'Avoid',
  alert: 'Alert',
}

export function normalizeSuggestionAction(action?: string, label?: string): SuggestionAction | null {
  const raw = (action || label || '').toLowerCase()
  if (!raw) return null
  if (raw === 'buy') return 'buy'
  if (raw === 'add' || raw === 'increase') return 'add'
  if (raw === 'reduce' || raw === 'decrease') return 'reduce'
  if (raw === 'sell') return 'sell'
  if (raw === 'hold') return 'hold'
  if (raw === 'watch' || raw === 'neutral') return 'watch'
  if (raw === 'avoid') return 'avoid'
  if (raw === 'alert') return 'alert'
  if (/\bbuy\b|open position|plan to open/.test(raw)) return 'buy'
  if (/\badd\b|overweight|top up|consider adding|plan to add|prepare to add/.test(raw)) return 'add'
  if (/reduce|underweight|trim/.test(raw)) return 'reduce'
  if (/\bsell\b|\bexit|stop loss/.test(raw)) return 'sell'
  if (/\bhold/.test(raw)) return 'hold'
  if (/watch|neutral|\bwait/.test(raw)) return 'watch'
  if (/avoid/.test(raw)) return 'avoid'
  return null
}

export function resolveSuggestionAction(action?: string, label?: string): SuggestionAction {
  return normalizeSuggestionAction(action, label) || 'watch'
}

export function resolveSuggestionLabel(action?: string, label?: string, fallback = 'Watch'): string {
  const normalized = normalizeSuggestionAction(action, label)
  if (normalized) return suggestionActionLabels[normalized] || fallback
  return String(label || '').trim() || fallback
}

export function resolveSuggestionColorClass(action?: string, label?: string): string {
  const normalized = resolveSuggestionAction(action, label)
  return suggestionActionColors[normalized] || suggestionActionColors.watch
}
