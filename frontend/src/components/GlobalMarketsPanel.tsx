import { Change } from '@/components/common/Change'
import { useEffect, useState } from 'react'
import { Globe } from 'lucide-react'
import { globalMarketsApi, type GlobalCue, type GlobalMarkets } from '@candlewise/api/global-markets'

const GROUP_ORDER: GlobalCue['group'][] = ['US', 'Asia', 'Europe', 'Commodities', 'Currency']
const REFRESH_MS = 60_000

export function formatLevel(value: number | null, key: string): string {
  if (value == null) return '--'
  const digits = key === 'USDINR' ? 2 : value >= 1000 ? 0 : 2
  return value.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/** Read-only world cues for context (owner decision 2026-09-25). Labelled with data quality. */
export default function GlobalMarketsPanel() {
  const [data, setData] = useState<GlobalMarkets | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let active = true
    const load = () =>
      globalMarketsApi
        .get()
        .then((d) => {
          if (active) {
            setData(d)
            setFailed(false)
          }
        })
        .catch(() => active && setFailed(true))
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [])

  if (!data?.enabled) {
    return failed ? (
      <div className="card p-3 mb-3 text-[12px] text-muted-foreground" data-testid="global-markets">
        Global markets are unavailable right now.
      </div>
    ) : null
  }

  return (
    <section className="card p-3 mb-3" data-testid="global-markets" aria-label="Global markets">
      <div className="mb-2 flex items-center gap-2">
        <Globe className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        <h2 className="font-display text-[16px] font-semibold">Global markets</h2>
        <span
          className="rounded border border-note-border bg-note px-1.5 py-0.5 text-[10px] text-note-foreground"
          title={`Source: ${data.source}. Context only; not investment advice.`}
        >
          {data.quality_label}
        </span>
      </div>
      <div className="grid grid-cols-1 gap-x-6 gap-y-1.5 min-[420px]:grid-cols-2 lg:grid-cols-5">
        {GROUP_ORDER.flatMap((group) => data.cues.filter((c) => c.group === group)).map((c) => (
          <div key={c.key} className="flex items-baseline justify-between gap-2 text-[12px]" data-testid={`cue-${c.key}`}>
            <span className="truncate text-muted-foreground">{c.name}</span>
            <span className="shrink-0 font-mono">
              {formatLevel(c.last, c.key)}{' '}
              {c.change_pct != null && <Change value={c.change_pct} />}
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
