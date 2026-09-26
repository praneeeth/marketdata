import { HoverPopover } from '@candlewise/base-ui/components/ui/hover-popover'
import type { KlineSummaryData } from '@candlewise/biz-ui/components/kline-summary-dialog'
import { TechnicalBadge } from '@candlewise/biz-ui/components/technical-badge'

interface KlineIndicatorsProps {
  summary: KlineSummaryData
}

export function KlineIndicators({ summary: s }: KlineIndicatorsProps) {
  return (
    <div className="space-y-3">
      {/* Trend and patterns (with explanations) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.trend && (
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
              </div>
            }
            trigger={<TechnicalBadge label={s.trend} tone="neutral" help />}
          />
        )}

        {s.macd_status && (
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
              </div>
            }
            trigger={<TechnicalBadge label={`MACD ${s.macd_status}`} tone="neutral" help />}
          />
        )}

        {s.rsi_status && (
          <HoverPopover
            title="RSI (relative strength)"
            content={
              <div className="space-y-2">
                <div>
                  <span className="font-medium text-foreground">What it is: </span>
                  RSI measures the relative strength of gains versus losses over a period (0-100). This shows RSI6 (the last 6 trading days).
                </div>
                <div>
                  <span className="font-medium text-foreground">Reference thresholds:</span>
                  <ul className="list-disc pl-4 mt-1 space-y-1">
                    <li>RSI6 &gt; 80: overbought (higher pullback risk)</li>
                    <li>RSI6 70-80: strong (bullish momentum)</li>
                    <li>RSI6 &lt; 20: oversold (a rebound is more likely)</li>
                  </ul>
                </div>
              </div>
            }
            trigger={
              <TechnicalBadge
                label={`RSI ${s.rsi_status}${s.rsi6 != null ? ` (${s.rsi6.toFixed(0)})` : ''}`}
                tone={s.rsi_status === 'overbought' ? 'warning' : s.rsi_status === 'oversold' ? 'info' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kdj_status && (
          <HoverPopover
            title="KDJ (turning points / overbought-oversold)"
            content={<div>J is the most sensitive; golden/death crosses help spot short-term turns but are easily disturbed in sideways markets, so combine them with trend and volume.</div>}
            trigger={<TechnicalBadge label={`KDJ ${s.kdj_status}`} tone="neutral" help />}
          />
        )}

        {s.volume_trend && (
          <HoverPopover
            title="Volume (confirmation)"
            content={<div>Rising volume often confirms a breakout or rebound; moves on falling volume tend to be weak. More reliable combined with trend and key levels.</div>}
            trigger={
              <TechnicalBadge
                label={`${s.volume_trend}${s.volume_ratio != null ? ` (${s.volume_ratio.toFixed(1)}x)` : ''}`}
                tone={s.volume_trend === 'volume up' ? 'warning' : s.volume_trend === 'volume down' ? 'info' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.boll_status && (
          <HoverPopover
            title="Bollinger bands (volatility/deviation)"
            content={<div>Moves above the upper band or below the lower band are common in trending phases or extreme volatility. Confirm with volume and a retest or hold.</div>}
            trigger={
              <TechnicalBadge
                label={`Bollinger: ${s.boll_status}`}
                tone={s.boll_status === 'above upper band' ? 'bullish' : s.boll_status === 'below lower band' ? 'bearish' : 'neutral'}
                help
              />
            }
          />
        )}

        {s.kline_pattern && (
          <HoverPopover
            title="Candlestick pattern (local structure)"
            content={<div>A single-candle pattern means little on its own; what matters is where it appears (trend, near support/resistance) and the volume behind it.</div>}
            trigger={<TechnicalBadge label={s.kline_pattern} tone="warning" help />}
          />
        )}
      </div>

      {/* Support and resistance (with explanations) */}
      <div className="flex flex-wrap gap-2 text-[11px]">
        {s.support != null && (
          <HoverPopover
            title="Support (key support zone)"
            content={<div>Near support, a fall is more likely to stop and bounce; a break on heavy volume can turn it into resistance. Think of it as a zone rather than a single price.</div>}
            trigger={<TechnicalBadge label={`Support ${s.support.toFixed(2)}`} tone="bullish" help />}
          />
        )}
        {s.resistance != null && (
          <HoverPopover
            title="Resistance (key resistance zone)"
            content={<div>The closer to resistance, the harder the move up; after a breakout on volume that holds, old resistance often becomes support.</div>}
            trigger={<TechnicalBadge label={`Resistance ${s.resistance.toFixed(2)}`} tone="bearish" help />}
          />
        )}
      </div>
    </div>
  )
}
