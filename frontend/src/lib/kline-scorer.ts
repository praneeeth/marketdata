import type { KlineSummaryData } from '@candlewise/biz-ui/components/kline-summary-dialog'

export type Action = 'buy' | 'add' | 'reduce' | 'sell' | 'hold' | 'watch' | 'avoid'

export interface KlineEvidenceItem {
  text: string
  details?: string
  delta: number
  tag?: string
}

export interface KlineScoreSuggestion {
  action: Action
  action_label: string
  signal: string
  score: number
  evidence: KlineEvidenceItem[]
  tags: string[]
}

export function buildKlineSuggestion(s: KlineSummaryData, holding?: boolean): KlineScoreSuggestion {
  let score = 0
  const items: KlineEvidenceItem[] = []
  const tags: string[] = []

  const fmt = (n?: number | null, digits: number = 2): string => {
    if (n == null || Number.isNaN(n)) return '--'
    return Number(n).toFixed(digits)
  }

  const tf = s.timeframe || '1d'
  const asof = s.asof ? `as of ${s.asof}` : ''
  const ctx = `interval ${tf} ${asof}`.trim()

  const addItem = (text: string, delta: number = 0, tag?: string, details?: string) => {
    items.push({ text, delta, tag, details })
    score += delta
    if (tag) tags.push(tag)
  }

  // Status strings come from the backend's kline_collector (English vocabulary).
  const has = (value: string | null | undefined, needle: string) => (value || '').toLowerCase().includes(needle)
  const maLine = `${ctx} · MA5/10/20: ${fmt(s.ma5)}/${fmt(s.ma10)}/${fmt(s.ma20)}`

  // Trend
  if (has(s.trend, 'bullish')) {
    addItem('Bullish MA alignment; trend strong', 2, 'Bullish trend', maLine)
  } else if (has(s.trend, 'bearish')) {
    addItem('Bearish MA alignment; trend weak', -2, 'Bearish trend', maLine)
  } else if (has(s.trend, 'mixed')) {
    addItem('Mixed MAs; no clear trend', 0, undefined, maLine)
  }

  // MACD
  if (has(s.macd_status, 'golden cross')) {
    addItem('MACD golden cross; short-term momentum strong', 2, 'MACD golden cross', `${ctx} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (has(s.macd_status, 'death cross')) {
    addItem('MACD death cross; short-term momentum weakening', -2, 'MACD death cross', `${ctx} · hist: ${fmt(s.macd_hist, 3)}`)
  }
  if (s.macd_hist != null) {
    if (s.macd_hist > 0.0) {
      addItem('MACD histogram positive (momentum bullish)', 1, undefined, `${ctx} · hist: ${fmt(s.macd_hist, 3)}`)
    } else if (s.macd_hist < 0.0) {
      addItem('MACD histogram negative (momentum bearish)', -1, undefined, `${ctx} · hist: ${fmt(s.macd_hist, 3)}`)
    }
  }

  // RSI
  if (has(s.rsi_status, 'oversold')) {
    addItem('RSI oversold; a rebound is possible', 1, 'RSI oversold', `${ctx} · RSI6: ${fmt(s.rsi6, 1)} (threshold <20)`)
  } else if (has(s.rsi_status, 'strong')) {
    addItem('RSI strong; buyers in control', 1, 'RSI strong', `${ctx} · RSI6: ${fmt(s.rsi6, 1)} (threshold 70-80)`)
  } else if (has(s.rsi_status, 'overbought')) {
    addItem('RSI overbought; watch for a pullback', -1, 'RSI overbought', `${ctx} · RSI6: ${fmt(s.rsi6, 1)} (threshold >80)`)
  } else if (has(s.rsi_status, 'weak')) {
    addItem('RSI weak; short-term pressure', -1, 'RSI weak', `${ctx} · RSI6: ${fmt(s.rsi6, 1)} (threshold <30)`)
  } else if (has(s.rsi_status, 'neutral')) {
    addItem('RSI neutral', 0, undefined, `${ctx} · RSI6: ${fmt(s.rsi6, 1)}`)
  }

  // KDJ
  const kdjLine = `${ctx} · K/D/J: ${fmt(s.kdj_k, 1)}/${fmt(s.kdj_d, 1)}/${fmt(s.kdj_j, 1)}`
  if (has(s.kdj_status, 'golden cross')) {
    addItem('KDJ golden cross; turning stronger short term', 1, 'KDJ golden cross', kdjLine)
  }
  if (has(s.kdj_status, 'death cross')) {
    addItem('KDJ death cross; turning weaker short term', -1, 'KDJ death cross', kdjLine)
  }

  // BOLL
  if (has(s.boll_status, 'above upper band')) {
    addItem('Above the upper Bollinger band; strong trend', 1, 'Above upper band', `${ctx} · close: ${fmt(s.last_close)} · upper: ${fmt(s.boll_upper)}`)
  } else if (has(s.boll_status, 'below lower band')) {
    addItem('Below the lower Bollinger band; weak', -1, 'Below lower band', `${ctx} · close: ${fmt(s.last_close)} · lower: ${fmt(s.boll_lower)}`)
  }

  // Volume
  if (has(s.volume_trend, 'volume up')) {
    addItem('Volume rising; more participation', 1, 'Volume up', `${ctx} · volume ratio: ${fmt(s.volume_ratio, 1)}x`)
  } else if (has(s.volume_trend, 'volume down')) {
    addItem('Volume falling; momentum lacking', -1, 'Volume down', `${ctx} · volume ratio: ${fmt(s.volume_ratio, 1)}x`)
  }

  // Support / Resistance proximity
  if (s.last_close != null && s.support != null && s.support > 0) {
    if (s.last_close <= s.support * 1.02) {
      const dist = (s.last_close - s.support) / s.support * 100
      addItem('Price near support; a bounce is more likely', 1, 'Near support', `${ctx} · close: ${fmt(s.last_close)} · support: ${fmt(s.support)} · distance: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (threshold <=+2%)`)
    }
  }
  if (s.last_close != null && s.resistance != null && s.resistance > 0) {
    if (s.last_close >= s.resistance * 0.98) {
      const dist = (s.last_close - s.resistance) / s.resistance * 100
      addItem('Price near resistance; limited upside', -1, 'Near resistance', `${ctx} · close: ${fmt(s.last_close)} · resistance: ${fmt(s.resistance)} · distance: ${dist >= 0 ? '+' : ''}${dist.toFixed(1)}% (threshold >=-2%)`)
    }
  }

  const holdingFlag = holding === true
  let action: Action
  if (holdingFlag) {
    if (score >= 3) action = 'add'
    else if (score >= 1) action = 'hold'
    else if (score <= -3) action = 'sell'
    else if (score <= -1) action = 'reduce'
    else action = 'watch'
  } else {
    if (score >= 3) action = 'buy'
    else if (score <= -2) action = 'avoid'
    else action = 'watch'
  }

  const uniqTags = Array.from(new Set(tags))
  const signal = uniqTags.length > 0 ? uniqTags.join(' / ') : 'Technicals neutral'

  const actionLabel = (a: Action): string => {
    switch (a) {
      case 'buy': return 'Buy'
      case 'add': return 'Add'
      case 'reduce': return 'Reduce'
      case 'sell': return 'Sell'
      case 'hold': return 'Hold'
      case 'watch': return 'Watch'
      case 'avoid': return 'Avoid'
      default: return 'Watch'
    }
  }

  return {
    action,
    action_label: actionLabel(action),
    signal,
    score,
    evidence: items,
    tags: uniqTags,
  }
}
