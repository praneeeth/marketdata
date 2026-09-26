import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft, Bell, Brain, Eye, MessageCircle, RefreshCw } from 'lucide-react'
import { insightApi, stocksApi, tradingAgentsApi, type DeepAnalysisResult, type StockItem } from '@candlewise/api'
import { Button } from '@candlewise/base-ui/components/ui/button'
import StockInsightModal from '@candlewise/biz-ui/components/stock-insight-modal'
import InteractiveKline from '@candlewise/biz-ui/components/InteractiveKline'
import { DeepAnalysisModal } from '@candlewise/biz-ui/components/deep-analysis-modal'
import { Change } from '@/components/common/Change'
import { EmptyState, ErrorState, LoadingState, errorMessage } from '@/components/common/states'
import ResearchSummary, { type SourceItem } from '@/components/research/ResearchSummary'
import { formatCompact, formatINR, formatIST } from '@/lib/format'

interface Quote {
  symbol: string
  name?: string
  current_price?: number | null
  change_pct?: number | null
  change_amount?: number | null
  prev_close?: number | null
  open_price?: number | null
  high_price?: number | null
  low_price?: number | null
  volume?: number | null
  source?: string
  quality?: string
}

interface KlineSummary {
  asof?: string
  last_close?: number
  trend?: string
  macd_status?: string
  rsi6?: number
  rsi_status?: string
  kdj_status?: string
  boll_status?: string
  volume_trend?: string
  change_5d?: number
  change_20d?: number
  support_s?: number
  support_m?: number
  support_l?: number
  resistance_s?: number
  resistance_m?: number
  resistance_l?: number
  kline_pattern?: string
}

interface NewsItem {
  source: string
  source_label?: string
  title: string
  publish_time: string
  url: string
}

type Async<T> = { loading: boolean; error: string; data: T }

function useAsync<T>(load: () => Promise<T>, initial: T, deps: unknown[]): Async<T> & { reload: () => void } {
  const [state, setState] = useState<Async<T>>({ loading: true, error: '', data: initial })
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    setState((s) => ({ ...s, loading: true, error: '' }))
    load()
      .then((data) => alive && setState({ loading: false, error: '', data }))
      .catch((e) => alive && setState({ loading: false, error: errorMessage(e), data: initial }))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  return { ...state, reload: () => setTick((t) => t + 1) }
}

const QUALITY_LABEL: Record<string, string> = {
  unofficial_delayed: 'Delayed / unofficial',
  delayed: 'Delayed',
  realtime: 'Live',
  live: 'Live',
}

function Stat({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-[11px] text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 truncate text-[14px] font-medium tabular-nums text-foreground">{children}</dd>
    </div>
  )
}

/** Stock detail: price and key facts first, then the research summary, chart, technicals and news. */
export default function StockDetailPage() {
  const params = useParams<{ symbol: string }>()
  const symbol = decodeURIComponent(params.symbol || '').trim().toUpperCase()
  const market = 'IN'
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null)
  const [insightOpen, setInsightOpen] = useState(false)
  const [deepOpen, setDeepOpen] = useState(false)

  const stocks = useAsync<StockItem[]>(() => stocksApi.list(), [], [])
  const stock = useMemo(() => stocks.data.find((s) => s.symbol.toUpperCase() === symbol) || null, [stocks.data, symbol])

  const quote = useAsync<Quote | null>(
    () => insightApi.quote<Quote>(symbol, market).then((q) => { setFetchedAt(new Date()); return q }),
    null,
    [symbol],
  )
  const summary = useAsync<KlineSummary | null>(
    () => insightApi.klineSummary<{ summary: KlineSummary }>(symbol, market).then((r) => r?.summary || null),
    null,
    [symbol],
  )
  const research = useAsync<DeepAnalysisResult | null>(() => tradingAgentsApi.getLatestForStock(symbol), null, [symbol])
  const news = useAsync<NewsItem[]>(
    () => insightApi.news<NewsItem[]>({ symbols: symbol, hours: 168, limit: 8 }).then((r) => r || []),
    [],
    [symbol],
  )

  const name = stock?.name || quote.data?.name || symbol
  const q = quote.data
  const s = summary.data
  const researchMarkdown = research.data ? research.data.raw_data?.research_summary || research.data.content : null
  const analysisDate = (research.data as (DeepAnalysisResult & { timestamp?: string }) | null)?.timestamp || null

  const sources: SourceItem[] = useMemo(() => {
    const out: SourceItem[] = []
    if (q?.source) {
      out.push({
        label: 'Quote',
        title: `${q.source}${q.quality ? ` (${QUALITY_LABEL[q.quality] || q.quality})` : ''}`,
        time: fetchedAt?.toISOString() || null,
      })
    }
    if (s?.asof) out.push({ label: 'Daily candles', title: 'Indicators and levels', time: s.asof })
    if (analysisDate) out.push({ label: 'Deep research', title: 'Multi-agent research summary', time: analysisDate })
    for (const n of news.data.slice(0, 5)) out.push({ label: n.source_label || n.source, title: n.title, url: n.url, time: n.publish_time })
    return out
  }, [q, s, analysisDate, news.data, fetchedAt])

  const askAssistant = useCallback(() => {
    window.dispatchEvent(new CustomEvent('candlewise-open-chat', { detail: { symbol, market, stockName: name, pageContext: 'stock_detail' } }))
  }, [symbol, name])

  const reloadAll = () => {
    quote.reload()
    summary.reload()
    research.reload()
    news.reload()
  }

  if (!symbol) {
    return <EmptyState title="No stock selected" description="Pick a stock from your portfolio or watchlist." action={<Link to="/portfolio" className="text-primary">Go to portfolio</Link>} />
  }

  return (
    <div className="space-y-5 md:space-y-6">
      <Link to="/portfolio" className="inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        Portfolio
      </Link>

      {/* Price header */}
      <section className="card p-4 md:p-5" aria-label="Price">
        <div className="flex flex-col gap-3 md:flex-row md:items-end md:justify-between">
          <div className="min-w-0">
            <div className="eyebrow">{symbol} · NSE</div>
            <h1 className="page-title truncate">{name}</h1>
            {quote.loading ? (
              <LoadingState rows={1} label="Loading the price…" className="p-0 pt-2" />
            ) : quote.error ? (
              <ErrorState title="Couldn't load the price" message={quote.error} onRetry={quote.reload} className="items-start px-0 py-3 text-left" />
            ) : q?.current_price != null ? (
              <div className="mt-1 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span className="text-[30px] font-semibold tabular-nums text-foreground">{formatINR(q.current_price, { compact: false })}</span>
                <Change value={q.change_pct} className="text-[16px] font-semibold" />
                <Change value={q.change_amount} kind="number" arrow={false} className="text-[14px]" />
              </div>
            ) : (
              <p className="mt-2 text-[13px] text-muted-foreground">No price available. Connect a broker under Data sources.</p>
            )}
            {q && (
              <p className="mt-1 text-[12px] text-muted-foreground">
                {fetchedAt ? `Fetched ${formatIST(fetchedAt, 'time', { suffix: true })}` : ''}
                {q.quality ? ` · ${QUALITY_LABEL[q.quality] || q.quality}` : ''}
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => setDeepOpen(true)} disabled={!stock} title={stock ? undefined : 'Add this stock to your watchlist to run deep research'}>
              <Brain className="h-4 w-4" aria-hidden="true" /> Deep research
            </Button>
            <Button size="sm" variant="outline" onClick={askAssistant}>
              <MessageCircle className="h-4 w-4" aria-hidden="true" /> Ask
            </Button>
            <Button size="sm" variant="outline" onClick={() => setInsightOpen(true)}>
              <Eye className="h-4 w-4" aria-hidden="true" /> Quick view
            </Button>
            <Button size="sm" variant="ghost" asChild>
              <Link to="/alerts"><Bell className="h-4 w-4" aria-hidden="true" /> Alerts</Link>
            </Button>
            <Button size="sm" variant="ghost" onClick={reloadAll} aria-label="Refresh">
              <RefreshCw className="h-4 w-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        {q && q.current_price != null && (
          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 border-t border-border/70 pt-4 sm:grid-cols-3 md:grid-cols-6">
            <Stat label="Open">{formatINR(q.open_price, { compact: false })}</Stat>
            <Stat label="High">{formatINR(q.high_price, { compact: false })}</Stat>
            <Stat label="Low">{formatINR(q.low_price, { compact: false })}</Stat>
            <Stat label="Prev close">{formatINR(q.prev_close, { compact: false })}</Stat>
            <Stat label="Volume">{formatCompact(q.volume, 2)}</Stat>
            <Stat label="5d / 20d">
              <Change value={s?.change_5d} arrow={false} /> <span className="text-muted-foreground">/</span> <Change value={s?.change_20d} arrow={false} />
            </Stat>
          </dl>
        )}
      </section>

      <div className="grid gap-5 md:gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="min-w-0 space-y-5 md:space-y-6">
          <ResearchSummary
            markdown={researchMarkdown}
            analysisDate={analysisDate}
            detailHref={analysisDate ? `/analysis/${encodeURIComponent(symbol)}/${analysisDate.slice(0, 10)}` : undefined}
            lastClose={s?.last_close ?? q?.current_price ?? null}
            levels={
              s
                ? { support: [s.support_s, s.support_m, s.support_l], resistance: [s.resistance_s, s.resistance_m, s.resistance_l], asof: s.asof }
                : null
            }
            sources={sources}
            loading={research.loading}
            error={research.error}
            onRetry={research.reload}
            emptyAction={
              stock ? (
                <Button size="sm" onClick={() => setDeepOpen(true)}>
                  <Brain className="h-4 w-4" aria-hidden="true" /> Run deep research
                </Button>
              ) : (
                <span className="text-[12px] text-muted-foreground">Add this stock to your watchlist to run deep research.</span>
              )
            }
          />

          <section className="card overflow-hidden" aria-label="Price chart">
            <header className="border-b border-border/70 px-4 py-3 md:px-5">
              <h2 className="font-display text-[17px] font-semibold">Price chart</h2>
            </header>
            <div className="p-2 md:p-4">
              <InteractiveKline symbol={symbol} market={market} />
            </div>
          </section>
        </div>

        <aside className="min-w-0 space-y-5 md:space-y-6">
          <section className="card p-4" aria-label="Technical snapshot">
            <h2 className="font-display text-[17px] font-semibold">Technical snapshot</h2>
            <p className="mb-3 text-[12px] text-muted-foreground">Descriptive readings from daily candles, not signals.</p>
            {summary.loading ? (
              <LoadingState rows={4} label="Loading indicators…" className="p-0" />
            ) : summary.error ? (
              <ErrorState message={summary.error} onRetry={summary.reload} className="py-4" />
            ) : !s ? (
              <EmptyState title="No candle data yet" className="py-4" />
            ) : (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-2.5 text-[13px]">
                <Stat label="Trend">{s.trend || '—'}</Stat>
                <Stat label="MACD">{s.macd_status || '—'}</Stat>
                <Stat label={`RSI(6)${s.rsi6 != null ? ` ${s.rsi6.toFixed(0)}` : ''}`}>{s.rsi_status || '—'}</Stat>
                <Stat label="KDJ">{s.kdj_status || '—'}</Stat>
                <Stat label="Bollinger">{s.boll_status || '—'}</Stat>
                <Stat label="Volume">{s.volume_trend || '—'}</Stat>
                {s.kline_pattern && <Stat label="Pattern">{s.kline_pattern}</Stat>}
              </dl>
            )}
          </section>

          <section className="card p-4" aria-label="News and filings">
            <h2 className="font-display text-[17px] font-semibold">News &amp; filings</h2>
            <p className="mb-2 text-[12px] text-muted-foreground">Last 7 days, times in IST.</p>
            {news.loading ? (
              <LoadingState rows={3} label="Loading news…" className="p-0" />
            ) : news.error ? (
              <ErrorState message={news.error} onRetry={news.reload} className="py-4" />
            ) : news.data.length === 0 ? (
              <EmptyState title="No recent news" description="Nothing mentioning this stock in the last 7 days." className="py-4" />
            ) : (
              <ul className="divide-y divide-border/70">
                {news.data.map((n, i) => (
                  <li key={`${n.url}-${i}`} className="py-2">
                    <a href={n.url} target="_blank" rel="noopener noreferrer" className="text-[13px] leading-snug text-foreground hover:underline">
                      {n.title}
                    </a>
                    <div className="mt-0.5 text-[11px] text-muted-foreground">
                      {n.source_label || n.source} · <time dateTime={n.publish_time}>{formatIST(n.publish_time, 'short', { suffix: true })}</time>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>

      <StockInsightModal open={insightOpen} onOpenChange={setInsightOpen} symbol={symbol} market={market} stockName={name} />
      {stock && (
        <DeepAnalysisModal
          open={deepOpen}
          onOpenChange={(open) => {
            setDeepOpen(open)
            if (!open) research.reload()
          }}
          stockId={stock.id}
          stockName={name}
          stockSymbol={symbol}
          initialResult={research.data}
        />
      )}
    </div>
  )
}
