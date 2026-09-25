import { useEffect, useState } from 'react'
import { Globe } from 'lucide-react'
import { globalMarketsApi, type GlobalCue, type GlobalMarkets } from '@panwatch/api/global-markets'

const GROUP_ORDER: GlobalCue['group'][] = ['US', 'Asia', 'Europe', 'Commodities', 'Currency']
const REFRESH_MS = 60_000

export function formatLevel(value: number | null, key: string): string {
  if (value == null) return '--'
  const digits = key === 'USDINR' ? 2 : value >= 1000 ? 0 : 2
  return value.toLocaleString('en-IN', { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

function changeClass(pct: number | null): string {
  if (pct == null || pct === 0) return 'text-muted-foreground'
  return pct > 0 ? 'text-emerald-600' : 'text-rose-600'
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
        <Globe className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold">Global markets</h2>
        <span
          className="rounded border border-amber-500/40 px-1.5 py-0.5 text-[10px] text-amber-600"
          title={`Source: ${data.source}. Context only; not investment advice.`}
        >
          {data.quality_label}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 sm:grid-cols-3 lg:grid-cols-5">
        {GROUP_ORDER.flatMap((group) => data.cues.filter((c) => c.group === group)).map((c) => (
          <div key={c.key} className="flex items-baseline justify-between gap-2 text-[12px]" data-testid={`cue-${c.key}`}>
            <span className="truncate text-muted-foreground">{c.name}</span>
            <span className="shrink-0 font-mono">
              {formatLevel(c.last, c.key)}{' '}
              <span className={changeClass(c.change_pct)}>
                {c.change_pct != null ? `${c.change_pct > 0 ? '+' : ''}${c.change_pct.toFixed(2)}%` : ''}
              </span>
            </span>
          </div>
        ))}
      </div>
    </section>
  )
}
