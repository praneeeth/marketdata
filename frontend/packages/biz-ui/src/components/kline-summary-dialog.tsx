import { useCallback, useEffect, useState } from 'react'
import { Sparkles } from 'lucide-react'
import { fetchAPI } from '@candlewise/api'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@candlewise/base-ui/components/ui/dialog'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { buildKlineSuggestion } from '@/lib/kline-scorer'
import { HoverPopover } from '@candlewise/base-ui/components/ui/hover-popover'
import { TechnicalBadge, technicalToneFromSuggestionAction } from '@candlewise/biz-ui/components/technical-badge'
import { useCompliance } from '@/hooks/use-compliance'

export interface KlineSummaryData {
  // meta (from backend)
  timeframe?: string
  computed_at?: string
  asof?: string
  params?: Record<string, any>

  last_close?: number | null
  recent_5_up?: number | null
  trend?: string
  macd_status?: string
  macd_cross?: string | null
  macd_cross_days?: number | null
  macd_hist?: number | null
  rsi6?: number | null
  rsi_status?: string
  kdj_k?: number | null
  kdj_d?: number | null
  kdj_j?: number | null
  kdj_status?: string
  volume_ratio?: number | null
  volume_trend?: string
  boll_upper?: number | null
  boll_mid?: number | null
  boll_lower?: number | null
  boll_width?: number | null
  boll_status?: string
  ma5?: number | null
  ma10?: number | null
  ma20?: number | null
  ma60?: number | null
  kline_pattern?: string | null
  support?: number | null
  resistance?: number | null
  support_s?: number | null
  support_m?: number | null
  support_l?: number | null
  resistance_s?: number | null
  resistance_m?: number | null
  resistance_l?: number | null
  change_5d?: number | null
  change_20d?: number | null
  amplitude?: number | null
  amplitude_avg5?: number | null
}

interface KlineSummaryResponse {
  symbol: string
  market: string
  summary: KlineSummaryData
}

interface KlineSummaryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  symbol: string
  market: string
  stockName?: string
  hasPosition?: boolean
  initialSummary?: KlineSummaryData | null
}

function formatLocalDateTime(iso?: string): string {
  if (!iso) return ''
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })
  } catch {
    return ''
  }
}

export function KlineSummaryDialog({
  open,
  onOpenChange,
  symbol,
  market,
  stockName,
  hasPosition,
  initialSummary = null,
}: KlineSummaryDialogProps) {
  const [loading, setLoading] = useState(false)
  const [summary, setSummary] = useState<KlineSummaryData | null>(null)
  const [error, setError] = useState<string | null>(null)
  // The rule-based action label is advice computed in the browser, outside the backend
  // output guard, so it only exists when recommendation features are enabled (ADR-004).
  const { isEnabled } = useCompliance()
  const scoringEnabled = isEnabled('suggestion_pool')

  const buildSuggestion = (s: KlineSummaryData, holding?: boolean) => {
    const scored = buildKlineSuggestion(s, holding)
    const items: Array<{ text: string; delta: number }> = []
    let localScore = 0

    const add = (text: string, delta: number) => { items.push({ text, delta }); localScore += delta }

    if (s.trend?.includes('bullish')) add('Bullish MA alignment; trend strong', 2)
    else if (s.trend?.includes('bearish')) add('Bearish MA alignment; trend weak', -2)

    if (s.macd_status?.includes('golden cross')) add('MACD golden cross; short-term momentum strong', 2)
    if (s.macd_status?.includes('death cross')) add('MACD death cross; short-term momentum weakening', -2)
    if (typeof s.macd_hist === 'number') add(`MACD histogram ${s.macd_hist > 0 ? 'positive' : s.macd_hist < 0 ? 'negative' : 'near 0'}`, s.macd_hist > 0 ? 1 : s.macd_hist < 0 ? -1 : 0)

    if (s.rsi_status?.includes('oversold')) add('RSI oversold; a rebound is possible', 1)
    else if (s.rsi_status?.includes('strong')) add('RSI strong; buyers in control', 1)
    else if (s.rsi_status?.includes('overbought')) add('RSI overbought; watch for a pullback', -1)
    else if (s.rsi_status?.includes('weak')) add('RSI weak; short-term pressure', -1)

    if (s.kdj_status?.includes('golden cross')) add('KDJ golden cross; turning stronger short term', 1)
    if (s.kdj_status?.includes('death cross')) add('KDJ death cross; turning weaker short term', -1)

    if (s.boll_status?.includes('above upper band')) add('Above the upper Bollinger band; strong trend', 1)
    else if (s.boll_status?.includes('below lower band')) add('Below the lower Bollinger band; weak', -1)

    if (s.volume_trend?.includes('volume up')) add('Volume rising; more participation', 1)
    else if (s.volume_trend?.includes('volume down')) add('Volume falling; momentum lacking', -1)

    if (s.last_close != null && s.support != null && s.support > 0 && s.last_close <= s.support * 1.02) add('Price near support; a bounce is more likely', 1)
    if (s.last_close != null && s.resistance != null && s.resistance > 0 && s.last_close >= s.resistance * 0.98) add('Price near resistance; limited upside', -1)

    return { ...scored, score: localScore, items }
  }

  useEffect(() => {
    if (!open || !symbol) return

    // If we already have preloaded summary, use it without refetch
    if (initialSummary) {
      setSummary(initialSummary)
      setError(null)
      setLoading(false)
      return
    }

    setLoading(true)
    setError(null)
    setSummary(null)

    const m = market || 'IN'
    fetchAPI<KlineSummaryResponse>(`/klines/${encodeURIComponent(symbol)}/summary?market=${encodeURIComponent(m)}`)
      .then((data) => setSummary(data.summary || null))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [open, symbol, market, initialSummary])

  const effectiveSummary = initialSummary || summary
  const suggestion = scoringEnabled && effectiveSummary ? buildSuggestion(effectiveSummary, hasPosition) : null

  const handleAskAI = useCallback(() => {
    if (!effectiveSummary) return
    const s = effectiveSummary
    const parts: string[] = []
    const items = []
    if (s.trend) items.push(`trend ${s.trend}`)
    if (s.macd_status) items.push(`MACD${s.macd_status}${s.macd_hist != null ? `(hist=${s.macd_hist.toFixed(3)})` : ''}`)
    if (s.rsi_status) items.push(`RSI${s.rsi_status}${s.rsi6 != null ? `(${s.rsi6.toFixed(0)})` : ''}`)
    if (s.kdj_status) items.push(`KDJ${s.kdj_status}${s.kdj_k != null ? `(K=${s.kdj_k.toFixed(1)},D=${s.kdj_d?.toFixed(1)},J=${s.kdj_j?.toFixed(1)})` : ''}`)
    if (s.boll_status) items.push(`Bollinger ${s.boll_status}${s.boll_width != null ? ` (bandwidth ${s.boll_width.toFixed(1)}%)` : ''}`)
    if (s.volume_trend) items.push(`volume ${s.volume_trend}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`)
    if (items.length) parts.push(`Technical indicators: ${items.join(', ')}`)
    if (s.support != null) parts.push(`Support: ${s.support.toFixed(2)}`)
    if (s.resistance != null) parts.push(`Resistance: ${s.resistance.toFixed(2)}`)
    if (s.last_close != null) parts.push(`Close: ${s.last_close.toFixed(2)}`)
    if (s.change_5d != null) parts.push(`5-day change: ${s.change_5d.toFixed(2)}%`)
    if (s.change_20d != null) parts.push(`20-day change: ${s.change_20d.toFixed(2)}%`)
    if (s.ma5 != null) parts.push(`MAs: MA5=${s.ma5.toFixed(2)} MA10=${s.ma10?.toFixed(2)} MA20=${s.ma20?.toFixed(2)} MA60=${s.ma60?.toFixed(2)}`)
    if (suggestion) {
      parts.push(`Technical score: ${suggestion.action_label} (score=${suggestion.score}), signal: ${suggestion.signal || 'neutral'}`)
      if (suggestion.items.length) {
        parts.push(`Score basis: ${suggestion.items.map(e => `${e.text} (${e.delta > 0 ? '+' : ''}${e.delta})`).join('; ')}`)
      }
    }
    window.dispatchEvent(new CustomEvent('candlewise-open-chat', {
      detail: { symbol, market, stockName: stockName || symbol, pageContext: parts.join('\n') }
    }))
    onOpenChange(false)
  }, [effectiveSummary, suggestion, symbol, market, stockName, onOpenChange])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        className="max-w-md"
        onClick={(e) => e.stopPropagation()}
      >
        <DialogHeader>
          <DialogTitle>K-line / technical indicators</DialogTitle>
          <DialogDescription>
            <div className="space-y-0.5">
              <div>{stockName ? `${stockName} (${symbol})` : symbol}</div>
              {(effectiveSummary?.timeframe || effectiveSummary?.computed_at || effectiveSummary?.asof) && (
                <div className="text-[11px] text-muted-foreground/70">
                  {effectiveSummary?.timeframe ? `Interval: ${effectiveSummary.timeframe}` : 'Interval: 1d'}
                  {effectiveSummary?.asof ? ` · Data as of: ${effectiveSummary.asof}` : ''}
                  {effectiveSummary?.computed_at ? ` · Computed: ${formatLocalDateTime(effectiveSummary.computed_at)}` : ''}
                </div>
              )}
            </div>
          </DialogDescription>
        </DialogHeader>

        {!initialSummary && loading ? (
          <div className="text-[12px] text-muted-foreground">Loading...</div>
        ) : error ? (
          <div className="text-[12px] text-rose-500">{error}</div>
        ) : !effectiveSummary ? (
          <div className="text-[12px] text-muted-foreground">No data</div>
        ) : (
          <div className="space-y-3">
            {suggestion && (
              <div className="p-3 rounded-lg bg-accent/20 border border-border/30">
                <div className="flex items-center justify-between gap-2">
                  <TechnicalBadge
                    label={suggestion.action_label}
                    tone={technicalToneFromSuggestionAction(suggestion.action, suggestion.action_label)}
                    size="sm"
                  />
                  <span className="text-[10px] text-muted-foreground">
                    {hasPosition ? 'Held' : 'Not held'} · score {suggestion.score}
                  </span>
                </div>
                <div className="mt-2 text-[12px] text-foreground font-medium">
                  {suggestion.signal}
                </div>

                {suggestion.items.length > 0 && (
                  <div className="mt-2 space-y-1">
                    {suggestion.items.map((it, idx) => {
                      const color =
                        it.delta > 0 ? 'text-emerald-500' :
                        it.delta < 0 ? 'text-emerald-500' :
                        'text-muted-foreground'
                      return (
                        <div key={`${it.text}-${idx}`} className="flex items-center justify-between gap-3 text-[11px]">
                          <span className="text-muted-foreground">{it.text}</span>
                          <span className={`font-mono ${color}`}>
                            {it.delta > 0 ? '+' : ''}{it.delta}
                          </span>
                        </div>
                      )
                    })}
                  </div>
                )}

                <div className="mt-2 text-[10px] text-muted-foreground/70">
                  Generated from technical indicator rules only; not investment advice
                </div>
              </div>
            )}

            <div className="text-[10px] text-muted-foreground/60">
              Tip: hover over an indicator label for details
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary.trend && (
                <HoverPopover
                  title="Trend (moving average alignment)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        The trend label comes from the relative position of the moving averages (MA5/MA10/MA20), computed from daily closes. Shorter MAs react faster; longer ones are smoother.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Common readings:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Bullish alignment</span> (MA5 &gt; MA10 &gt; MA20): the uptrend is orderly; pullbacks usually look to MA5/MA10 for support.</li>
                          <li><span className="font-medium text-foreground">Bearish alignment</span> (MA5 &lt; MA10 &lt; MA20): the downtrend dominates; rallies to MA10/MA20 often meet resistance.</li>
                          <li><span className="font-medium text-foreground">Mixed MAs</span>: a sideways/consolidation phase; signals depend more on volume and key levels.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Now: {effectiveSummary.trend}</div>
                      {(effectiveSummary.ma5 != null || effectiveSummary.ma10 != null || effectiveSummary.ma20 != null || effectiveSummary.ma60 != null) && (
                        <div className="text-[10px] text-muted-foreground/70">
                          MAs: MA5≈{effectiveSummary.ma5 != null ? effectiveSummary.ma5.toFixed(2) : '—'}; MA10≈{effectiveSummary.ma10 != null ? effectiveSummary.ma10.toFixed(2) : '—'}; MA20≈{effectiveSummary.ma20 != null ? effectiveSummary.ma20.toFixed(2) : '—'}; MA60≈{effectiveSummary.ma60 != null ? effectiveSummary.ma60.toFixed(2) : '—'}
                        </div>
                      )}
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: moving averages lag, so they suit filtering the trend better than timing entries and exits on their own.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.trend} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.macd_status && (
                <HoverPopover
                  title="MACD (trend/momentum)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        MACD has two lines (DIF/DEA) and a histogram (hist). Usual definitions: DIF = EMA12 - EMA26, DEA = EMA(DIF, 9), hist ≈ (DIF - DEA) * 2.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Golden cross</span>: DIF crosses above DEA; short-term momentum turns from weak to strong.</li>
                          <li><span className="font-medium text-foreground">Death cross</span>: DIF crosses below DEA; short-term momentum turns from strong to weak.</li>
                          <li><span className="font-medium text-foreground">Positive/negative histogram</span>: positive usually means bullish momentum dominates; negative means bearish momentum dominates.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Now: {effectiveSummary.macd_status}{effectiveSummary.macd_hist != null ? `, histogram ${effectiveSummary.macd_hist > 0 ? 'positive' : effectiveSummary.macd_hist < 0 ? 'negative' : 'near 0'} (hist≈${effectiveSummary.macd_hist.toFixed(3)})` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: in sideways ranges MACD often gives false crosses; confirm with the trend (MAs) and price-volume.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`MACD ${effectiveSummary.macd_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary.rsi_status && (
                <HoverPopover
                  title="RSI (relative strength)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        RSI measures the relative strength of gains versus losses over a period (0-100). This shows RSI6 (the last 6 trading days).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Thresholds used here:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>RSI6 &gt; 80: overbought (higher pullback risk)</li>
                          <li>RSI6 70-80: strong (bullish momentum)</li>
                          <li>RSI6 &lt; 20: oversold (may rebound, but can stay oversold for long in a downtrend)</li>
                          <li>RSI6 20-30: weak (bearish momentum)</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Now: {effectiveSummary.rsi_status}{effectiveSummary.rsi6 != null ? `, RSI6≈${effectiveSummary.rsi6.toFixed(0)}` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: overbought doesn't mean an immediate fall, nor oversold an immediate rebound; it's more reliable to look for divergence/exhaustion alongside the trend and key levels.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`RSI ${effectiveSummary.rsi_status}${effectiveSummary.rsi6 != null ? ` (${effectiveSummary.rsi6.toFixed(0)})` : ''}`}
                      tone={effectiveSummary.rsi_status === 'overbought' ? 'warning' : effectiveSummary.rsi_status === 'oversold' ? 'info' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kdj_status && (
                <HoverPopover
                  title="KDJ (stochastic)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        KDJ is a momentum indicator showing where the price sits within a recent range (similar to a stochastic oscillator). The usual signal is a K/D golden or death cross.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Golden cross</span>: a hint of short-term strength, more useful in an uptrend.</li>
                          <li><span className="font-medium text-foreground">Death cross</span>: a hint of short-term weakness, more useful in a downtrend.</li>
                          <li>Extreme J values (&gt;100 or &lt;0) are often read as overbought/oversold, but can mislead in strong trends.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Now: {effectiveSummary.kdj_status}</div>
                        {(effectiveSummary.kdj_k != null || effectiveSummary.kdj_d != null || effectiveSummary.kdj_j != null) && (
                          <div>
                            K≈{effectiveSummary.kdj_k != null ? effectiveSummary.kdj_k.toFixed(1) : '—'}{' '}
                            D≈{effectiveSummary.kdj_d != null ? effectiveSummary.kdj_d.toFixed(1) : '—'}{' '}
                            J≈{effectiveSummary.kdj_j != null ? effectiveSummary.kdj_j.toFixed(1) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: KDJ can flip back and forth in sideways markets; use it with support and resistance.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={`KDJ ${effectiveSummary.kdj_status}`} tone="neutral" help />
                  }
                />
              )}

              {effectiveSummary?.volume_trend && (
                <HoverPopover
                  title="Volume (rising/falling)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Volume shows whether a move has trading behind it. The volume trend here comes from volume_ratio (today's volume / the 5-day average).
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to read it:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Volume up</span>: usually more participation; a rise on higher volume favours the trend continuing.</li>
                          <li><span className="font-medium text-foreground">Volume down</span>: may mean waiting or exhaustion; a fall on lower volume sometimes means selling pressure is easing.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Now: {effectiveSummary.volume_trend}{effectiveSummary.volume_ratio != null ? `, volume ratio≈${effectiveSummary.volume_ratio.toFixed(1)}x` : ''}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: read volume together with price direction (price up/volume up, price up/volume down, price down/volume up, price down/volume down).
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`${effectiveSummary.volume_trend}${effectiveSummary.volume_ratio != null ? ` (${effectiveSummary.volume_ratio.toFixed(1)}x)` : ''}`}
                      tone={effectiveSummary.volume_trend === 'volume up' ? 'warning' : effectiveSummary.volume_trend === 'volume down' ? 'info' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.boll_status && (
                <HoverPopover
                  title="Bollinger bands (volatility/channel)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Bollinger bands have a middle band (usually MA20) and upper/lower bands (middle ± 2 standard deviations), describing the price channel and changes in volatility.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li><span className="font-medium text-foreground">Above upper band</span>: strong short term, but may reverse after a spike; confirm with volume.</li>
                          <li><span className="font-medium text-foreground">Below lower band</span>: weak short term, but a panic fall can also bring an oversold rebound.</li>
                          <li>Narrowing bands usually mean volatility is contracting, often before a directional move; widening bands mean volatility is expanding.</li>
                        </ul>
                      </div>
                      <div>
                        <span className="font-medium text-foreground">Bandwidth thresholds used here:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Bandwidth &lt; 5: bands narrowing (more likely consolidation)</li>
                          <li>Bandwidth &gt; 15: bands widening (volatility expanding)</li>
                          <li>Otherwise: normal range</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>
                          Now: {effectiveSummary.boll_status}{effectiveSummary.boll_width != null ? `, bandwidth≈${effectiveSummary.boll_width.toFixed(1)}%` : ''}
                        </div>
                        {(effectiveSummary.boll_upper != null || effectiveSummary.boll_mid != null || effectiveSummary.boll_lower != null) && (
                          <div>
                            Upper≈{effectiveSummary.boll_upper != null ? effectiveSummary.boll_upper.toFixed(2) : '—'}; middle≈{effectiveSummary.boll_mid != null ? effectiveSummary.boll_mid.toFixed(2) : '—'}; lower≈{effectiveSummary.boll_lower != null ? effectiveSummary.boll_lower.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Bollinger: ${effectiveSummary.boll_status}`}
                      tone={effectiveSummary.boll_status === 'above upper band' ? 'bullish' : effectiveSummary.boll_status === 'below lower band' ? 'bearish' : 'neutral'}
                      help
                    />
                  }
                />
              )}

              {effectiveSummary?.kline_pattern && (
                <HoverPopover
                  title="Candlestick pattern (local structure)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        The pattern comes from recognising the shape of the last 1-2 candles (doji, hammer, engulfing, etc.); it is a local signal.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">What it means:</span>
                        Most patterns need confirmation from the trend, volume and key levels. A hammer means more at the end of a decline; an engulfing pattern depends on comparing the two candles.
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">Now: {effectiveSummary.kline_pattern}</div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: single-candle patterns are often wrong; treat them as hints, not as decisions on their own.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge label={effectiveSummary.kline_pattern} tone="warning" help />
                  }
                />
              )}
            </div>

            <div className="flex flex-wrap gap-2 text-[11px]">
              {effectiveSummary && effectiveSummary.support != null && (
                <HoverPopover
                  title="Support (key support zone)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Support is a price zone where buyers are more likely to appear. Near support, the price is more likely to stop falling, bounce or consolidate.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How it's calculated here:</span>
                        Support in this dialog is the lowest low of the last 20 trading days (min low), a medium-term reference level.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to use it:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>Treat it as a zone rather than an exact line; allow some tolerance (e.g. ±1-2%).</li>
                          <li>Near support, a halt on lower volume or a bounce on higher volume is usually more reliable; a break on heavy volume can turn support into resistance.</li>
                          <li>Useful for framing risk around key levels rather than predicting highs and lows.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Now: support≈{effectiveSummary.support.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.support > 0 && (
                          <div>
                            Distance (from the close)≈{(((effectiveSummary.last_close - effectiveSummary.support) / effectiveSummary.support) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close <= effectiveSummary.support * 1.02 ? ' (near support; the scoring rules add a point)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.support_s != null || effectiveSummary.support_m != null || effectiveSummary.support_l != null) && (
                          <div>
                            Levels: short (5d)≈{effectiveSummary.support_s != null ? effectiveSummary.support_s.toFixed(2) : '—'}; medium (20d)≈{effectiveSummary.support_m != null ? effectiveSummary.support_m.toFixed(2) : '—'}; long (60d)≈{effectiveSummary.support_l != null ? effectiveSummary.support_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: support/resistance are statistical key levels, not guaranteed turning points; a strong trend can cut straight through.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Support ${effectiveSummary.support.toFixed(2)}`}
                      tone="bullish"
                      help
                    />
                  }
                />
              )}
              {effectiveSummary && effectiveSummary.resistance != null && (
                <HoverPopover
                  title="Resistance (key resistance zone)"
                  content={
                    <div className="space-y-2">
                      <div>
                        <span className="font-medium text-foreground">What it is: </span>
                        Resistance is a price zone where sellers are more likely to appear. Near resistance, a rise is more likely to stall, pull back or turn sideways.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How it's calculated here:</span>
                        Resistance in this dialog is the highest high of the last 20 trading days (max high), a medium-term reference level.
                      </div>
                      <div>
                        <span className="font-medium text-foreground">How to use it:</span>
                        <ul className="list-disc pl-4 mt-1 space-y-1">
                          <li>The closer to resistance, the less room is left above; many traders wait for a breakout on volume that then holds on a retest.</li>
                          <li>If the price breaks resistance on volume and holds, old resistance often becomes new support.</li>
                          <li>Near resistance, watch for risk signs such as price-volume divergence or a spike that fades.</li>
                        </ul>
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 space-y-1">
                        <div>Now: resistance≈{effectiveSummary.resistance.toFixed(2)}</div>
                        {effectiveSummary.last_close != null && effectiveSummary.resistance > 0 && (
                          <div>
                            Distance (from the close)≈{(((effectiveSummary.resistance - effectiveSummary.last_close) / effectiveSummary.resistance) * 100).toFixed(2)}%
                            {' '}
                            {effectiveSummary.last_close >= effectiveSummary.resistance * 0.98 ? ' (near resistance; the scoring rules deduct a point)' : ''}
                          </div>
                        )}
                        {(effectiveSummary.resistance_s != null || effectiveSummary.resistance_m != null || effectiveSummary.resistance_l != null) && (
                          <div>
                            Levels: short (5d)≈{effectiveSummary.resistance_s != null ? effectiveSummary.resistance_s.toFixed(2) : '—'}; medium (20d)≈{effectiveSummary.resistance_m != null ? effectiveSummary.resistance_m.toFixed(2) : '—'}; long (60d)≈{effectiveSummary.resistance_l != null ? effectiveSummary.resistance_l.toFixed(2) : '—'}
                          </div>
                        )}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70">
                        Note: whether a breakout is real usually depends on volume and whether it holds or survives a retest; a brief poke through is often false.
                      </div>
                    </div>
                  }
                  trigger={
                    <TechnicalBadge
                      label={`Resistance ${effectiveSummary.resistance.toFixed(2)}`}
                      tone="bearish"
                      help
                    />
                  }
                />
              )}
            </div>

            {(effectiveSummary.change_5d != null || effectiveSummary.change_20d != null || effectiveSummary.amplitude != null) && (
              <div className="flex gap-4 text-[11px] text-muted-foreground">
                {effectiveSummary.change_5d != null && (
                  <HoverPopover
                    title="5-day change (short-term momentum)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          The 5-day change is the overall return of the last 5 trading days, a quick read on short-term momentum.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How it's calculated here:</span>
                          Today's close compared with the close 5 trading days ago: (Close[t] - Close[t-5]) / Close[t-5].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to read it:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Positive: strong short term; more likely to continue with rising volume or a bullish trend.</li>
                            <li>Negative: weak short term; riskier alongside bearish MAs and a MACD death cross.</li>
                            <li>A very large gain can also mean the move is overheated and prone to a pullback; frame risk around support and resistance.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Now: {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        5d{' '}
                        <span className={effectiveSummary.change_5d >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                          {effectiveSummary.change_5d >= 0 ? '+' : ''}{effectiveSummary.change_5d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.change_20d != null && (
                  <HoverPopover
                    title="20-day change (swing / one-month momentum)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          The 20-day change is roughly one trading month's return, closer to the swing trend.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How it's calculated here:</span>
                          Today's close compared with the close 20 trading days ago: (Close[t] - Close[t-20]) / Close[t-20].
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to read it:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>Positive with a bullish trend: usually going with the trend; on pullbacks watch support and volume.</li>
                            <li>Negative with a bearish trend: usually against the wind; rallies tend to stall near resistance.</li>
                            <li>5-day and 20-day disagreeing: may be a short-term bounce or pullback inside a larger trend; be careful calling a reversal.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70">
                          Now: {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        20d{' '}
                        <span className={effectiveSummary.change_20d >= 0 ? 'text-emerald-500' : 'text-rose-500'}>
                          {effectiveSummary.change_20d >= 0 ? '+' : ''}{effectiveSummary.change_20d.toFixed(2)}%
                        </span>
                      </span>
                    }
                  />
                )}
                {effectiveSummary.amplitude != null && (
                  <HoverPopover
                    title="Range (volatility)"
                    content={
                      <div className="space-y-2">
                        <div>
                          <span className="font-medium text-foreground">What it is: </span>
                          The range describes how far the price moved between the day's high and low, a measure of how volatile it is.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How it's calculated here:</span>
                          Today's range ≈ (High - Low) / Low. The larger it is, the more volatile the session, with more risk and more opportunity.
                        </div>
                        <div>
                          <span className="font-medium text-foreground">How to read it:</span>
                          <ul className="list-disc pl-4 mt-1 space-y-1">
                            <li>A wide range is common on volume breakouts, panic selling or news; use volume and trend to tell expansion from a collapse.</li>
                            <li>A narrow range is common in consolidation; with narrowing Bollinger bands, a directional move becomes more likely.</li>
                            <li>Wider ranges call for more caution; the same risk distance doesn't suit every level of volatility.</li>
                          </ul>
                        </div>
                        <div className="text-[10px] text-muted-foreground/70 space-y-1">
                          <div>Now: {effectiveSummary.amplitude.toFixed(2)}%</div>
                          {effectiveSummary.amplitude_avg5 != null && (
                            <div>5-day average: {effectiveSummary.amplitude_avg5.toFixed(2)}%</div>
                          )}
                        </div>
                      </div>
                    }
                    trigger={
                      <span className="cursor-help hover:text-foreground">
                        Range: {effectiveSummary.amplitude.toFixed(2)}%
                      </span>
                    }
                  />
                )}
              </div>
            )}

            {scoringEnabled && (
            <details className="group">
              <summary className="text-[11px] text-muted-foreground cursor-pointer hover:text-foreground">
                How the score works <span className="text-[10px]">(click to expand)</span>
              </summary>
              <div className="mt-2 text-[11px] text-muted-foreground whitespace-pre-wrap bg-accent/20 rounded p-2 space-y-2">
                <div className="font-medium text-foreground">Label rules (by whether you hold it)</div>
                <div className="space-y-1">
                  <div>Not held: score ≥ 3 → Buy; score ≤ -2 → Avoid; otherwise → Watch</div>
                  <div>Held: score ≥ 3 → Add; score ≥ 1 → Hold; score ≤ -3 → Sell; score ≤ -1 → Reduce; otherwise → Watch</div>
                </div>
                <div className="font-medium text-foreground">Scoring rules (added up; 0 is neutral)</div>
                <div className="space-y-1">
                  <div>Trend (MAs): bullish alignment +2; bearish alignment -2</div>
                  <div>MACD: golden cross +2; death cross -2; positive histogram +1; negative histogram -1</div>
                  <div>RSI: oversold +1; strong +1; overbought -1; weak -1</div>
                  <div>KDJ: golden cross +1; death cross -1</div>
                  <div>Bollinger: above upper band +1; below lower band -1</div>
                  <div>Volume: volume up +1; volume down -1</div>
                  <div>Support/resistance: close ≤ support × 1.02 → +1; close ≥ resistance × 0.98 → -1</div>
                </div>
              </div>
            </details>
            )}

            <Button variant="secondary" size="sm" className="w-full mt-1" onClick={handleAskAI}>
              <Sparkles className="w-3.5 h-3.5 mr-1" /> Ask AI about these indicators
            </Button>

          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
