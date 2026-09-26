import { type DeepAnalysisResult } from '@panwatch/api'
import { normalizeSuggestionAction } from '@panwatch/biz-ui/components/suggestion-action'
import ShareCardDialog from './ShareCardDialog'

interface ShareCardModalProps {
  open: boolean
  onClose: () => void
  result: DeepAnalysisResult
  symbol: string
  date: string
}

/**
 * 5-level rating -> display label + colours (green up, red down).
 * Same normalisation as technical-badge / suggestion-action: buy/overweight = green (bullish), sell/underweight = red (bearish), hold = amber (neutral).
 * Explicit, self-contained hex colours so the exported PNG is right in any theme (light/dark).
 */
const RATING_VISUAL: Record<
  string,
  { label: string; color: string; soft: string; gradFrom: string; gradTo: string }
> = {
  // Bullish (green)
  buy: { label: 'Buy', color: '#059669', soft: '#ecfdf5', gradFrom: '#34d399', gradTo: '#059669' },
  add: { label: 'Overweight', color: '#059669', soft: '#ecfdf5', gradFrom: '#6ee7b7', gradTo: '#059669' },
  // Neutral (amber)
  hold: { label: 'Hold', color: '#d97706', soft: '#fffbeb', gradFrom: '#fbbf24', gradTo: '#d97706' },
  // Bearish (red)
  reduce: { label: 'Underweight', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fda4af', gradTo: '#e11d48' },
  sell: { label: 'Sell', color: '#e11d48', soft: '#fff1f2', gradFrom: '#fb7185', gradTo: '#e11d48' },
}
const RATING_FALLBACK = {
  label: 'Watch',
  color: '#475569',
  soft: '#f8fafc',
  gradFrom: '#94a3b8',
  gradTo: '#475569',
}
const REVIEW_VISUAL = {
  label: 'Needs manual review',
  color: '#c2410c',
  soft: '#fff7ed',
  gradFrom: '#fb923c',
  gradTo: '#c2410c',
}

/** Map the backend's possible 5-level raw values (overweight/underweight) to words the normaliser knows. */
function mapRatingRaw(raw?: string): string | undefined {
  if (!raw) return undefined
  const r = raw.toLowerCase().trim()
  if (r === 'overweight') return 'add'
  if (r === 'underweight') return 'reduce'
  return r
}

/**
 * Parse the stock name + symbol from the title: drop a leading bracketed tag such as [Deep research] and a trailing ": rating".
 * e.g. "[Deep research] Tata Motors (TATAMOTORS): Hold" -> "Tata Motors (TATAMOTORS)"
 */
function parseStockName(title: string, symbol: string): string {
  let s = (title || '').trim()
  s = s.replace(/^\[[^\]]*\]\s*/, '') // drop the first leading [...] tag
  s = s.replace(/:\s*[^:]*$/, '') // drop a trailing ":xxx" (rating)
  s = s.trim()
  return s || symbol
}

/**
 * Clean the conclusion into one paragraph: strip markdown bold ** and a leading "Action: x Reasoning:" prefix.
 * Extra whitespace collapses to single spaces for line-clamp display.
 */
function cleanConclusion(text: string): string {
  let s = (text || '').replace(/\*\*/g, '')
  s = s.replace(/^Action\s*:\s*\S+\s*Reasoning\s*:\s*/i, '')
  s = s.replace(/\s+/g, ' ').trim()
  return s
}

export default function ShareCardModal({ open, onClose, result, symbol, date }: ShareCardModalProps) {
  const sug = result.raw_data?.suggestion
  // Rating source: the backend's raw 5-level value first, otherwise action, then action_label as a fallback.
  const ratingRaw = mapRatingRaw(sug?.rating_raw)
  const normalized = normalizeSuggestionAction(ratingRaw || sug?.action, sug?.action_label)
  const reviewRequired = sug?.review_required === true || sug?.rating_raw === 'review'
  const visual = reviewRequired ? REVIEW_VISUAL : (normalized && RATING_VISUAL[normalized]) || RATING_FALLBACK

  const stockName = parseStockName(result.title || '', symbol)
  const confidence = sug?.confidence
  const costUsd = result.raw_data?.cost_usd
  const conclusion = cleanConclusion(sug?.signal || sug?.reason || '')
  const confPct = Math.max(0, Math.min(100, (confidence ?? 0) * 10))

  return (
    <ShareCardDialog open={open} onClose={onClose} filename={`${stockName}-${date}-analysis-card`}>
      {/* Header: stock name + symbol / date */}
      <div
        style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'space-between',
          gap: 12,
        }}
      >
        <div style={{ fontSize: 24, fontWeight: 800, lineHeight: 1.2, color: '#0f172a' }}>
          {stockName}
        </div>
        <div style={{ fontSize: 14, color: '#94a3b8', fontWeight: 500, flexShrink: 0 }}>{date}</div>
      </div>

      {/* Hero: big rating + confidence bar + cost */}
      <div
        style={{
          marginTop: 20,
          borderRadius: 18,
          padding: '22px 24px',
          background: `linear-gradient(135deg, ${visual.gradFrom} 0%, ${visual.gradTo} 100%)`,
          color: '#ffffff',
          boxShadow: `0 10px 30px -8px ${visual.color}66`,
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              letterSpacing: 1,
              opacity: 0.92,
              flexShrink: 0,
            }}
          >
            AI research conclusion
          </div>
          <div
            style={{
              fontSize: 42,
              fontWeight: 900,
              lineHeight: 1,
              letterSpacing: 2,
              marginLeft: 'auto',
            }}
          >
            {visual.label}
          </div>
        </div>

        {/* Confidence bar */}
        <div style={{ marginTop: 18 }}>
          <div
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: 12.5,
              opacity: 0.92,
              marginBottom: 6,
            }}
          >
            <span>Confidence</span>
            <span style={{ fontWeight: 700 }}>
              {confidence != null ? confidence.toFixed(1) : '-'} / 10
            </span>
          </div>
          <div
            style={{
              height: 8,
              borderRadius: 999,
              background: 'rgba(255,255,255,0.3)',
              overflow: 'hidden',
            }}
          >
            <div
              style={{
                height: '100%',
                width: `${confPct}%`,
                borderRadius: 999,
                background: '#ffffff',
              }}
            />
          </div>
          <div style={{ marginTop: 10, fontSize: 12, opacity: 0.85 }}>
            Analysis cost ${costUsd != null ? costUsd.toFixed(4) : '-'}
          </div>
        </div>
      </div>

      {/* Conclusion paragraph: about 5 lines at most */}
      {conclusion && (
        <div
          style={{
            marginTop: 22,
            fontSize: 15.5,
            lineHeight: 1.7,
            color: '#334155',
            display: '-webkit-box',
            WebkitLineClamp: 5,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
          }}
        >
          {conclusion}
        </div>
      )}

      {/* Subtitle for TradingAgents cards (9 agents), above the shell divider/footer */}
      <div style={{ marginTop: 22, fontSize: 12, color: '#94a3b8', lineHeight: 1.6 }}>
        AI research team (9 agents) deep research
      </div>
    </ShareCardDialog>
  )
}
