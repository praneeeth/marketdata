import { useEffect, useState, type ReactNode } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ArrowLeft,
  FileDown,
  ImageDown,
  List,
  ChevronDown,
  Target,
  TrendingUp,
  MessageSquare,
  Newspaper,
  BarChart3,
  Scale,
  ShieldAlert,
  History,
  FileText,
  type LucideIcon,
} from 'lucide-react'
import {
  tradingAgentsApi,
  type DeepAnalysisResult,
  type HistoryComparisonResponse,
} from '@candlewise/api'
import { Switch } from '@candlewise/base-ui/components/ui/switch'
import { buildAnalysisSections } from '@candlewise/biz-ui/analysis-sections'
import ShareCardModal from '../components/ShareCardModal'
import { useCompliance } from '@/hooks/use-compliance'

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-up',
  hold: 'text-amber-500',
  sell: 'text-down',
}

/** An icon per section (decision/technical/sentiment/news/fundamentals/debate/risk), matching the ids from buildAnalysisSections */
const SECTION_ICON: Record<string, LucideIcon> = {
  summary: FileText,
  decision: Target,
  market: TrendingUp,
  social: MessageSquare,
  news: Newspaper,
  fundamentals: BarChart3,
  debate: Scale,
  risk: ShieldAlert,
}

/** localStorage key for the sub-heading toggle (remembers the user's choice) */
const TOC_SUB_KEY = 'candlewise_toc_show_sub'

/** India is the only market. */
function inferMarket(_symbol: string): string {
  return 'IN' // India is the only market
}

function pctClass(v: number | null | undefined): string {
  if (v == null) return 'text-muted-foreground'
  return v > 0 ? 'text-up' : v < 0 ? 'text-down' : 'text-muted-foreground'
}

function fmtPct(v: number | null | undefined): string {
  if (v == null) return '-'
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}

/** Heading -> anchor slug (strips markdown emphasis/hashes/emoji; whitespace becomes hyphens).
 *  The table of contents and the rendered headings use the same logic, so ids match and links work. */
function slugify(text: string): string {
  return text
    .trim()
    .toLowerCase()
    .replace(/[*_`#~]/g, '')
    .replace(/\s+/g, '-')
    .replace(/[^\p{L}\p{N}_-]/gu, '')
    .replace(/-+/g, '-')
    .replace(/^-|-$/g, '')
}

/** Recursively take the plain text of a ReactMarkdown heading node's children (for the anchor id). */
function nodeText(children: ReactNode): string {
  if (typeof children === 'string') return children
  if (typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children && typeof children === 'object' && 'props' in children) {
    return nodeText((children as { props?: { children?: ReactNode } }).props?.children)
  }
  return ''
}

/** Extract level 2-4 headings from markdown (for the sub-headings). */
function parseHeadings(markdown: string): { text: string; slug: string }[] {
  const out: { text: string; slug: string }[] = []
  for (const raw of markdown.split('\n')) {
    const m = /^(#{2,4})\s+(.+?)\s*#*$/.exec(raw)
    if (!m) continue
    const text = m[2].replace(/[*_`]/g, '').trim()
    if (text) out.push({ text, slug: slugify(m[2]) })
  }
  return out
}

export default function AnalysisDetailPage() {
  const { symbol = '', date = '' } = useParams()
  const navigate = useNavigate()
  const { isEnabled, shortDisclaimer } = useCompliance()
  const ratingEnabled = isEnabled('tradingagents_rating')
  const [result, setResult] = useState<DeepAnalysisResult | null>(null)
  const [history, setHistory] = useState<HistoryComparisonResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeId, setActiveId] = useState('')
  const [tocOpen, setTocOpen] = useState(false)
  const [showSub, setShowSub] = useState(() => {
    try {
      return localStorage.getItem(TOC_SUB_KEY) !== '0'
    } catch {
      return true
    }
  })
  const [pdfBusy, setPdfBusy] = useState(false)
  const [shareOpen, setShareOpen] = useState(false)

  const handleExportPdf = async () => {
    if (pdfBusy) return
    setPdfBusy(true)
    try {
      await tradingAgentsApi.downloadAnalysisPdf(symbol, date)
    } catch (e) {
      alert(e instanceof Error ? e.message : 'Export failed')
    } finally {
      setPdfBusy(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    tradingAgentsApi
      .getAnalysisByDate(symbol, date)
      .then(setResult)
      .catch(() => setResult(null))
      .finally(() => setLoading(false))
    tradingAgentsApi
      .getHistoryComparison(symbol, inferMarket(symbol), 90)
      .then(setHistory)
      .catch(() => setHistory(null))
  }, [symbol, date])

  // Remember the sub-heading toggle
  useEffect(() => {
    try {
      localStorage.setItem(TOC_SUB_KEY, showSub ? '1' : '0')
    } catch {
      /* ignore */
    }
  }, [showSub])

  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  const sug = ratingEnabled ? rawData.suggestion : undefined
  const reviewRequired = sug?.review_required === true || sug?.rating_raw === 'review'
  const decisionLabel = reviewRequired ? 'Needs manual review' : sug?.action_label
  const decisionColor = reviewRequired ? 'text-orange-500' : (sug ? DECISION_COLOR[sug.action] || '' : '')
  const sections = buildAnalysisSections(rawData, { includeDecision: ratingEnabled })
  const stats = ratingEnabled ? history?.stats : undefined
  const items = ratingEnabled ? history?.items || [] : []

  // Full table of contents: each section (level 1) + its markdown level 2-4 headings (level 2) + the past decision comparison
  const fullToc: { id: string; title: string; level: 0 | 1 }[] = []
  for (const s of sections) {
    fullToc.push({ id: `sec-${s.id}`, title: s.title, level: 0 })
    for (const h of parseHeadings(s.markdown)) {
      fullToc.push({ id: `h-${s.id}-${h.slug}`, title: h.text, level: 1 })
    }
  }
  if (ratingEnabled) fullToc.push({ id: 'sec-history', title: 'Past decision comparison', level: 0 })
  // The toggle decides whether sub-headings are shown and tracked
  const toc = showSub ? fullToc : fullToc.filter((t) => t.level === 0)

  // Scroll tracking: highlight the current section while scrolling (the highest heading in view, below the sticky nav)
  useEffect(() => {
    if (!result) return
    const els = toc
      .map((t) => document.getElementById(t.id))
      .filter((el): el is HTMLElement => !!el)
    if (!els.length) return
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        if (visible[0]) setActiveId(visible[0].target.id)
      },
      { rootMargin: '-100px 0px -55% 0px', threshold: 0 },
    )
    els.forEach((el) => observer.observe(el))
    return () => observer.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, toc.length])

  if (loading) {
    return <div className="p-12 text-center text-muted-foreground">Loading...</div>
  }
  if (!result) {
    return (
      <div className="p-12 text-center text-muted-foreground space-y-3">
        <div>No deep research record for {symbol} on {date}</div>
        <button onClick={() => navigate(-1)} className="text-primary hover:underline">
          Back
        </button>
      </div>
    )
  }

  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  // Title of the current section (shown on the mobile collapsed bar so users know where they are)
  const currentTitle = toc.find((t) => t.id === activeId)?.title || ''

  // Markdown headings: the same anchor ids as the table of contents + top margin (clear of the sticky nav)
  const headingComponents = (sectionId: string) => {
    const make = (Tag: 'h2' | 'h3' | 'h4') =>
      function Heading({ children }: { children?: ReactNode }) {
        const id = `h-${sectionId}-${slugify(nodeText(children))}`
        return (
          <Tag id={id} className="scroll-mt-24">
            {children}
          </Tag>
        )
      }
    return { h2: make('h2'), h3: make('h3'), h4: make('h4') }
  }

  // Table of contents header (title + sub-heading toggle), shared by the desktop sidebar and mobile dropdown
  const tocHeader = (
    <div className="flex items-center justify-between gap-2 mb-2 px-2">
      <span className="text-[11px] font-medium text-muted-foreground/70">Contents</span>
      <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="cursor-pointer select-none" onClick={() => setShowSub((v) => !v)}>
          Sub-headings
        </span>
        <Switch checked={showSub} onCheckedChange={setShowSub} />
      </div>
    </div>
  )

  // Table of contents list (shared by the desktop sidebar and mobile dropdown); onAfter closes the mobile dropdown after a choice
  const tocNav = (onAfter?: () => void) => (
    <nav className="space-y-0.5 text-[13px]">
      {toc.map((t) => (
        <button
          key={t.id}
          onClick={() => {
            scrollTo(t.id)
            onAfter?.()
          }}
          className={`block w-full text-left py-1 rounded-md transition-colors truncate ${
            t.level === 1 ? 'pl-5 pr-2 text-[12px]' : 'px-2'
          } ${
            activeId === t.id
              ? 'bg-accent text-foreground font-medium'
              : 'text-muted-foreground hover:text-foreground hover:bg-accent/50'
          }`}
        >
          {t.title}
        </button>
      ))}
    </nav>
  )

  return (
    <div className="min-h-screen">
      <div className="max-w-5xl mx-auto px-4 pb-12 flex gap-8">
        {/* Left column: header + body (the header spans only the left column, clear of the table of contents) */}
        <div className="flex-1 min-w-0 max-w-3xl">
          {/* Top bar */}
          <div className="border-b border-border/40 pb-3 mb-4 flex items-center gap-3">
            <button
              onClick={() => navigate(-1)}
              className="w-8 h-8 rounded-lg flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-accent transition-all shrink-0"
              aria-label="Back"
            >
              <ArrowLeft className="w-4 h-4" />
            </button>
            <h1 className="text-base font-bold truncate min-w-0">{result.title || `${symbol} deep research`}</h1>
            <span className="text-[12px] text-muted-foreground shrink-0">{date}</span>
            <button
              onClick={() => setShareOpen(true)}
              className="ml-auto shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all"
              title="Generate a shareable summary card image"
            >
              <ImageDown className="w-3.5 h-3.5" />
              Share image
            </button>
            <button
              onClick={handleExportPdf}
              disabled={pdfBusy}
              className="shrink-0 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-border/50 text-[12.5px] text-muted-foreground hover:text-foreground hover:bg-accent transition-all disabled:opacity-50"
              title="Export as a PDF file"
            >
              <FileDown className="w-3.5 h-3.5" />
              {pdfBusy ? 'Exporting…' : 'Export PDF'}
            </button>
          </div>

          {/* Body */}
          <article>
          {/* Decision summary (at the top of the body on mobile; in the right sidebar on desktop, see the aside below) */}
          {sug && (
            <div className="lg:hidden rounded-xl bg-accent/30 p-4 mb-6 flex items-center gap-3 flex-wrap">
              <span className={`text-[24px] font-bold ${decisionColor}`}>
                {decisionLabel}
              </span>
              {reviewRequired && <span className="text-[12px] text-orange-600">The data or conclusion is uncertain; verify it yourself before relying on it</span>}
              <span className="text-[13px] text-muted-foreground">
                Confidence {sug.confidence?.toFixed(1) ?? '-'} / 10
              </span>
              <span className="ml-auto text-[11px] text-muted-foreground">
                Cost ${rawData.cost_usd?.toFixed(4) ?? '-'}
              </span>
            </div>
          )}

          {/* Mobile table of contents: a sticky collapsed bar showing the current section; opens an overlay dropdown that closes on choice/outside click (hidden on desktop) */}
          <div className="lg:hidden sticky top-16 z-30 mb-6">
            <div className="relative">
              <button
                onClick={() => setTocOpen((o) => !o)}
                className="w-full flex items-center gap-2 px-3.5 py-2.5 rounded-xl border border-border/50 bg-card/95 backdrop-blur text-[13px] font-medium shadow-sm"
              >
                <List className="w-4 h-4 shrink-0" />
                <span className="truncate">{currentTitle || 'Contents'}</span>
                <ChevronDown
                  className={`w-4 h-4 ml-auto shrink-0 transition-transform ${tocOpen ? 'rotate-180' : ''}`}
                />
              </button>
              {tocOpen && (
                <>
                  <div className="fixed inset-0 z-0" onClick={() => setTocOpen(false)} />
                  <div className="absolute left-0 right-0 top-full mt-1 z-10 rounded-xl border border-border/50 bg-card/95 backdrop-blur shadow-lg max-h-[60vh] overflow-y-auto scrollbar p-2">
                    {tocHeader}
                    {tocNav(() => setTocOpen(false))}
                  </div>
                </>
              )}
            </div>
          </div>

          {/* Long-form sections */}
          {sections.map((s) => {
            const Icon = SECTION_ICON[s.id]
            return (
              <section key={s.id} id={`sec-${s.id}`} className="mb-12 scroll-mt-24">
                <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
                  {Icon && <Icon className="w-[18px] h-[18px] text-primary/70 shrink-0" />}
                  {s.title}
                </h2>
                <div className="prose prose-base dark:prose-invert max-w-none leading-relaxed prose-headings:mt-6 prose-headings:mb-2 prose-h2:text-[16px] prose-h3:text-[15px] prose-h4:text-[14px] prose-h2:font-semibold prose-h3:font-semibold prose-p:my-3 prose-p:text-foreground/90 prose-li:my-1 prose-table:my-4 prose-th:px-3 prose-th:py-2 prose-td:px-3 prose-td:py-2 prose-strong:text-foreground">
                  <ReactMarkdown remarkPlugins={[remarkGfm]} components={headingComponents(s.id)}>
                    {s.markdown}
                  </ReactMarkdown>
                </div>
              </section>
            )
          })}

          {/* Past decision comparison */}
          {ratingEnabled && (
          <section id="sec-history" className="mb-10 scroll-mt-24">
            <h2 className="flex items-center gap-2 text-[18px] font-bold mb-4 pb-2 border-b border-border/40">
              <History className="w-[18px] h-[18px] text-primary/70 shrink-0" />
              Past decisions vs actual moves
            </h2>
            {stats && (
              <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-4 text-[13px]">
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Overall hit rate</div>
                  <div className="font-bold">{stats.overall_hit_rate != null ? `${(stats.overall_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Buy hit rate</div>
                  <div className="font-bold">{stats.buy_hit_rate != null ? `${(stats.buy_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Sell hit rate</div>
                  <div className="font-bold">{stats.sell_hit_rate != null ? `${(stats.sell_hit_rate * 100).toFixed(0)}%` : '-'}</div>
                </div>
                <div className="rounded-lg bg-accent/30 p-3">
                  <div className="text-[11px] text-muted-foreground mb-1">Average 20-day return</div>
                  <div className={`font-bold ${pctClass(stats.avg_return_20d_pct)}`}>{fmtPct(stats.avg_return_20d_pct)}</div>
                </div>
              </div>
            )}
            {items.length > 0 ? (
              <div className="overflow-x-auto">
                <table className="w-full text-[13px]">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-[12px]">
                      <th className="text-left py-2 pr-3">Date</th>
                      <th className="text-left py-2 px-2">Decision</th>
                      <th className="text-right py-2 px-2">Analysis price</th>
                      <th className="text-right py-2 px-2">1d</th>
                      <th className="text-right py-2 px-2">5d</th>
                      <th className="text-right py-2 px-2">20d</th>
                      <th className="text-right py-2 pl-2">Hit</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((it, i) => (
                      <tr key={i} className="border-b border-border/50">
                        <td className="py-2 pr-3">{it.analysis_date}</td>
                        <td className="py-2 px-2">{it.action_label}{it.confidence != null ? ` (${it.confidence.toFixed(1)})` : ''}</td>
                        <td className="text-right py-2 px-2">{it.price_at_analysis ?? '-'}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_1d_pct)}`}>{fmtPct(it.return_1d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_5d_pct)}`}>{fmtPct(it.return_5d_pct)}</td>
                        <td className={`text-right py-2 px-2 ${pctClass(it.return_20d_pct)}`}>{fmtPct(it.return_20d_pct)}</td>
                        <td className="text-right py-2 pl-2">{it.hit_20d == null ? '-' : it.hit_20d ? '✓' : '✗'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="text-[13px] text-muted-foreground py-4">No past decisions</div>
            )}
          </section>
          )}

          {/* Disclaimer */}
          <div className="text-[11px] text-muted-foreground/70 italic border-t border-border/30 pt-4">
            {shortDisclaimer}
          </div>
          </article>
        </div>

        {/* Right column: final decision + table of contents in one card (starts level with the title; theme tokens for light/dark) */}
        <aside className="hidden lg:block w-52 shrink-0">
          <div className="sticky top-24 rounded-xl border border-border bg-card overflow-hidden">
            {/* Final decision summary */}
            {sug && (
              <div className="p-3.5 border-b border-border">
                <div className="flex items-baseline justify-between gap-2">
                  <span className={`text-[22px] font-bold leading-none ${decisionColor}`}>
                    {decisionLabel}
                  </span>
                  <span className="text-[11px] text-muted-foreground shrink-0">
                    ${rawData.cost_usd?.toFixed(4) ?? '-'}
                  </span>
                </div>
                {reviewRequired && (
                  <p className="mt-2 text-[11px] leading-4 text-orange-600">
                    Upstream couldn't safely produce an actionable rating; verify the data and report yourself.
                  </p>
                )}
                {sug.confidence != null && (
                  <div className="mt-2.5">
                    <div className="flex items-center justify-between text-[11px] text-muted-foreground mb-1">
                      <span>Confidence</span>
                      <span className="font-medium text-foreground">{sug.confidence.toFixed(1)} / 10</span>
                    </div>
                    <div className="h-1.5 rounded-full bg-muted overflow-hidden">
                      <div
                        className={`h-full rounded-full ${
                          sug.action === 'buy'
                            ? 'bg-success'
                            : sug.action === 'sell'
                              ? 'bg-destructive'
                              : 'bg-amber-500'
                        }`}
                        style={{ width: `${Math.max(0, Math.min(100, sug.confidence * 10))}%` }}
                      />
                    </div>
                  </div>
                )}
              </div>
            )}
            {/* Table of contents */}
            <div className="p-2">
              {tocHeader}
              <div className="max-h-[calc(100vh-19rem)] overflow-y-auto scrollbar">{tocNav()}</div>
            </div>
          </div>
        </aside>
      </div>

      {/* Share card (PNG export) */}
      <ShareCardModal
        open={shareOpen}
        onClose={() => setShareOpen(false)}
        result={result}
        symbol={symbol}
        date={date}
      />
    </div>
  )
}
