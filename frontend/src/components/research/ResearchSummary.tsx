import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ArrowDownRight, ArrowUpRight, CalendarDays, ExternalLink, FileText, Ruler } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatINR, formatIST, formatPct } from '@/lib/format'
import { isUnstructured, parseResearchSummary } from '@/lib/research-summary'
import { DisclaimerNote } from '@/components/common/Brand'
import { EmptyState, ErrorState, LoadingState } from '@/components/common/states'

export interface KeyLevels {
  support?: Array<number | null | undefined>
  resistance?: Array<number | null | undefined>
  asof?: string | null
}

export interface SourceItem {
  label: string
  title?: string
  url?: string
  time?: string | null
}

function Section({ title, icon, children, className }: { title: string; icon: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn('min-w-0', className)} aria-label={title}>
      <h3 className="mb-2 flex items-center gap-1.5 text-[13px] font-semibold text-foreground">
        <span aria-hidden="true" className="text-muted-foreground">{icon}</span>
        {title}
      </h3>
      {children}
    </section>
  )
}

function Items({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="text-[13px] text-muted-foreground">{empty}</p>
  return (
    <ul className="space-y-1.5 text-[14px] leading-relaxed text-foreground">
      {items.map((item, i) => (
        <li key={i} className="flex gap-2">
          <span aria-hidden="true" className="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-muted-foreground/60" />
          <span className="min-w-0 break-words">{item}</span>
        </li>
      ))}
    </ul>
  )
}

function uniqueLevels(values: Array<number | null | undefined> | undefined): number[] {
  const seen = new Set<number>()
  for (const v of values || []) if (typeof v === 'number' && Number.isFinite(v) && v > 0) seen.add(Math.round(v * 100) / 100)
  return [...seen]
}

function LevelRow({ label, values, lastClose }: { label: string; values: number[]; lastClose?: number | null }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span className="w-20 shrink-0 text-[12px] text-muted-foreground">{label}</span>
      {values.length ? (
        values.map((v) => (
          <span key={v} className="rounded-md border border-border/80 bg-background px-2 py-0.5 text-[13px] tabular-nums">
            {formatINR(v, { compact: false })}
            {lastClose ? (
              <span className="ml-1.5 text-[11px] text-muted-foreground">{formatPct(((v - lastClose) / lastClose) * 100, 1)}</span>
            ) : null}
          </span>
        ))
      ) : (
        <span className="text-[13px] text-muted-foreground">Not available</span>
      )}
    </div>
  )
}

/**
 * The research summary for one stock: summary, bull case, bear case, risks, key levels,
 * events and sources with IST timestamps. Research only: it never says what to do, and the
 * disclaimer is always shown.
 */
export default function ResearchSummary({
  markdown,
  analysisDate,
  detailHref,
  levels,
  lastClose,
  sources = [],
  loading = false,
  error,
  onRetry,
  emptyAction,
}: {
  markdown: string | null | undefined
  analysisDate?: string | null
  detailHref?: string
  levels?: KeyLevels | null
  lastClose?: number | null
  sources?: SourceItem[]
  loading?: boolean
  error?: string
  onRetry?: () => void
  emptyAction?: ReactNode
}) {
  const parsed = parseResearchSummary(markdown)
  const hasResearch = Boolean((markdown || '').trim())
  const support = uniqueLevels(levels?.support)
  const resistance = uniqueLevels(levels?.resistance)

  return (
    <article className="card overflow-hidden" aria-labelledby="research-summary-title">
      <header className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border/70 px-4 py-3 md:px-5">
        <div>
          <div className="eyebrow">Research summary</div>
          <h2 id="research-summary-title" className="font-display text-[19px] font-semibold text-foreground">
            What the evidence says
          </h2>
        </div>
        <div className="text-[12px] text-muted-foreground">
          {analysisDate ? <>As of {formatIST(analysisDate, analysisDate.length <= 10 ? 'date' : 'datetime', { suffix: true })}</> : null}
          {detailHref && (
            <Link to={detailHref} className="ml-3 font-medium text-primary hover:underline">
              Full report
            </Link>
          )}
        </div>
      </header>

      <div className="space-y-6 px-4 py-4 md:px-5 md:py-5">
        {loading ? (
          <LoadingState rows={5} label="Loading the research summary…" className="p-0" />
        ) : error ? (
          <ErrorState message={error} onRetry={onRetry} className="py-6" />
        ) : !hasResearch ? (
          <EmptyState
            title="No research summary yet"
            description="Run deep research to get a bull case, bear case, risks and events for this stock, drawn from its data and news."
            icon={<FileText className="h-5 w-5" />}
            action={emptyAction}
            className="py-6"
          />
        ) : (
          <>
            {parsed.summary && <p className="text-[15px] leading-relaxed text-foreground">{parsed.summary}</p>}
            {isUnstructured(parsed) && !parsed.summary && (
              <p className="whitespace-pre-wrap text-[14px] leading-relaxed text-foreground">{markdown}</p>
            )}
            <div className="grid gap-5 md:grid-cols-2">
              <Section title="Bull case" icon={<ArrowUpRight className="h-4 w-4" />} className="rounded-lg border border-border/80 p-3.5">
                <Items items={parsed.bull} empty="No bull points in this summary." />
              </Section>
              <Section title="Bear case" icon={<ArrowDownRight className="h-4 w-4" />} className="rounded-lg border border-border/80 p-3.5">
                <Items items={parsed.bear} empty="No bear points in this summary." />
              </Section>
            </div>
            <Section title="Key risks" icon={<AlertTriangle className="h-4 w-4" />}>
              <Items items={parsed.risks} empty="No specific risks listed." />
            </Section>
          </>
        )}

        <Section title="Key levels" icon={<Ruler className="h-4 w-4" />}>
          <div className="space-y-2">
            {levels !== undefined && (
              <>
                <LevelRow label="Support" values={support} lastClose={lastClose} />
                <LevelRow label="Resistance" values={resistance} lastClose={lastClose} />
              </>
            )}
            {parsed.levels.length > 0 ? (
              <Items items={parsed.levels} empty="" />
            ) : levels === undefined ? (
              <p className="text-[13px] text-muted-foreground">No levels mentioned in this report.</p>
            ) : null}
            {levels !== undefined && (
              <p className="text-[11px] text-muted-foreground">
                Computed from daily candles{levels?.asof ? ` up to ${formatIST(levels.asof, 'date')}` : ''}; % is the distance from the last close. Descriptive only.
              </p>
            )}
          </div>
        </Section>

        {hasResearch && (
          <Section title="Upcoming events" icon={<CalendarDays className="h-4 w-4" />}>
            <Items items={parsed.events} empty="No upcoming events mentioned." />
          </Section>
        )}

        <Section title="Sources" icon={<FileText className="h-4 w-4" />}>
          {sources.length === 0 && parsed.sources.length === 0 ? (
            <p className="text-[13px] text-muted-foreground">No sources yet.</p>
          ) : (
            <ul className="divide-y divide-border/70 text-[13px]">
              {sources.map((s, i) => (
                <li key={`s-${i}`} className="flex flex-wrap items-baseline justify-between gap-x-3 py-1.5">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium text-foreground">{s.label}</span>
                    {s.title && (
                      <>
                        <span className="text-muted-foreground"> · </span>
                        {s.url ? (
                          <a href={s.url} target="_blank" rel="noopener noreferrer" className="break-words text-foreground underline-offset-2 hover:underline">
                            {s.title}
                            <ExternalLink className="ml-1 inline h-3 w-3 text-muted-foreground" aria-label="(opens in a new tab)" />
                          </a>
                        ) : (
                          <span className="break-words text-foreground">{s.title}</span>
                        )}
                      </>
                    )}
                  </span>
                  <time dateTime={s.time || undefined} className="shrink-0 text-[12px] tabular-nums text-muted-foreground">
                    {s.time ? formatIST(s.time, 'short', { suffix: true }) : 'time not given'}
                  </time>
                </li>
              ))}
              {parsed.sources.map((s, i) => (
                <li key={`p-${i}`} className="py-1.5 text-foreground">
                  <span className="text-muted-foreground">Cited in the report: </span>
                  {s}
                </li>
              ))}
            </ul>
          )}
        </Section>

        <DisclaimerNote />
      </div>
    </article>
  )
}
