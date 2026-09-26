import { type StrategySignalItem } from '@candlewise/api'
import ShareCardDialog from './ShareCardDialog'

interface SignalScoreShareCardProps {
  open: boolean
  onClose: () => void
  item: StrategySignalItem
}

// Colours: bullish = green, bearish/neutral and so on
const UP = '#059669'
const NEUTRAL = '#d97706'
const SLATE = '#475569'

const MARKET_LABEL: Record<string, string> = { IN: 'India' }
const marketLabel = (m?: string) => (m ? MARKET_LABEL[m] || m : '')

/**
 * action -> display label + colours (as on the opportunities page: buy/add bullish green, hold neutral amber, the rest grey).
 * A hold when not held -> Watch; an add when not held -> Open position (matching displayActionLabel on Opportunities).
 */
function actionVisual(item: StrategySignalItem): { label: string; color: string } {
  const key = (item.action || '').toLowerCase()
  let label = item.action_label || item.action || 'Watch'
  if (!item.is_holding_snapshot && key === 'hold') label = 'Watch'
  if (!item.is_holding_snapshot && key === 'add') label = 'Open position'
  if (key === 'buy' || key === 'add') return { label, color: UP }
  if (key === 'hold') return { label, color: NEUTRAL }
  return { label, color: SLATE }
}

/** AI score (1-10) colour bands: ≥8 green (strong), 6-8 amber, <6 grey. */
function scoreColor(score: number): string {
  if (score >= 8) return UP
  if (score >= 6) return NEUTRAL
  return SLATE
}

function FactorList({
  title,
  color,
  bg,
  border,
  items,
  sign,
}: {
  title: string
  color: string
  bg: string
  border: string
  items: { label: string; contribution: number }[]
  sign: '+' | '-'
}) {
  if (!items.length) return null
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color, marginBottom: 8 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {items.map((f, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: 8,
              background: bg,
              border: `1px solid ${border}`,
              borderRadius: 8,
              padding: '7px 10px',
              fontSize: 12.5,
            }}
          >
            <span style={{ color: '#334155', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {f.label}
            </span>
            <span style={{ color, fontWeight: 800, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
              {sign}
              {Math.abs(f.contribution).toFixed(1)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Per-stock AI score card. Hero = AI score X/10 + action label + stock; positive/risk factors below come from factor_explain.
 */
export default function SignalScoreShareCard({ open, onClose, item }: SignalScoreShareCardProps) {
  const av = actionVisual(item)
  const aiScore = typeof item.ai_score === 'number' ? item.ai_score : null
  const heroColor = aiScore != null ? scoreColor(aiScore) : SLATE
  const name = item.stock_name || item.stock_symbol
  const positive = (item.factor_explain?.positive ?? []).slice(0, 4)
  const negative = (item.factor_explain?.negative ?? []).slice(0, 4)
  const summary = (item.signal || item.reason || '').replace(/\s+/g, ' ').trim()

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`ai-stock-score-${name}`}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: 12 }}>
        <div style={{ fontSize: 22, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>AI stock score</div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>
          {marketLabel(item.stock_market)}
        </div>
      </div>

      {/* Hero: AI score + action label + stock */}
      <div
        style={{
          marginTop: 18,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${heroColor} 0%, ${heroColor}cc 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${heroColor}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div style={{ minWidth: 0 }}>
            <div
              style={{
                fontSize: 22,
                fontWeight: 800,
                lineHeight: 1.2,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {name}
            </div>
            <div style={{ fontSize: 13, opacity: 0.9, marginTop: 4, fontVariantNumeric: 'tabular-nums' }}>
              {item.stock_market}:{item.stock_symbol}
            </div>
          </div>
          <div style={{ marginLeft: 'auto', textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontSize: 12, opacity: 0.92, fontWeight: 600, letterSpacing: 1 }}>AI score</div>
            <div style={{ fontSize: 44, fontWeight: 900, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {aiScore != null ? aiScore : '--'}
              <span style={{ fontSize: 20, opacity: 0.85 }}> / 10</span>
            </div>
          </div>
        </div>
        <div
          style={{
            marginTop: 14,
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            background: 'rgba(255,255,255,0.22)',
            borderRadius: 999,
            padding: '5px 14px',
            fontSize: 14,
            fontWeight: 700,
          }}
        >
          {av.label}
        </div>
      </div>

      {/* One-line signal */}
      {summary && (
        <div
          style={{
            marginTop: 16,
            fontSize: 14,
            lineHeight: 1.6,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {summary}
        </div>
      )}

      {/* Factor breakdown: positive (green) / risk (red) */}
      {(positive.length > 0 || negative.length > 0) && (
        <div style={{ marginTop: 18, display: 'flex', gap: 16, alignItems: 'flex-start' }}>
          <FactorList
            title="Positive factors"
            color="#059669"
            bg="#ecfdf5"
            border="#a7f3d0"
            items={positive}
            sign="+"
          />
          <FactorList title="Risk factors" color="#e11d48" bg="#fff1f2" border="#fecdd3" items={negative} sign="-" />
        </div>
      )}
    </ShareCardDialog>
  )
}
