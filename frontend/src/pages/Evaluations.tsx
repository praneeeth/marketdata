import { useCallback, useEffect, useMemo, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { CheckCircle2, ClipboardCheck, Clock3, RefreshCw, Target } from 'lucide-react'
import {
  evaluationsApi,
  type AgentPredictionFilters,
  type AgentPredictionGroup,
  type AgentPredictionListResponse,
  type AgentPredictionOutcomeItem,
  type AgentPredictionSummary,
  type EvaluationHorizonUnit,
} from '@candlewise/api'
import { Badge } from '@candlewise/base-ui/components/ui/badge'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@candlewise/base-ui/components/ui/dialog'
import { Input } from '@candlewise/base-ui/components/ui/input'
import { Label } from '@candlewise/base-ui/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@candlewise/base-ui/components/ui/select'
import { useToast } from '@candlewise/base-ui/components/ui/toast'
import { EmptyState, ErrorState, LoadingState, errorMessage } from '@/components/common/states'

type FilterState = {
  agentName: string
  market: string
  action: string
  status: string
  horizonUnit: EvaluationHorizonUnit
  days: number
  startDate: string
  endDate: string
}

const INITIAL_FILTERS: FilterState = {
  agentName: 'all', market: 'all', action: 'all', status: 'all',
  horizonUnit: 'trading_days', days: 90, startDate: '', endDate: '',
}

const ACTION_LABELS: Record<string, string> = {
  buy: 'Buy', add: 'Add', sell: 'Sell', reduce: 'Reduce', avoid: 'Avoid', hold: 'Hold', watch: 'Watch',
}

function toApiFilters(filters: FilterState): AgentPredictionFilters {
  return {
    agentName: filters.agentName === 'all' ? undefined : filters.agentName,
    market: filters.market === 'all' ? undefined : filters.market,
    action: filters.action === 'all' ? undefined : filters.action,
    status: filters.status === 'all' ? undefined : filters.status,
    horizonUnit: filters.horizonUnit, days: filters.days,
    startDate: filters.startDate || undefined, endDate: filters.endDate || undefined, limit: 200,
  }
}

function formatPct(value: number | null | undefined) {
  if (value == null) return '--'
  return `${value > 0 ? '+' : ''}${value.toFixed(2)}%`
}

function pctClass(value: number | null | undefined) {
  if (value == null || value === 0) return 'text-muted-foreground'
  return value > 0 ? 'text-up' : 'text-down'
}

function outcomeLabel(outcome?: AgentPredictionOutcomeItem) {
  if (!outcome) return 'Not recorded'
  if (outcome.status === 'pending') return 'Pending'
  if (outcome.status === 'no_base_price') return 'No base price'
  return outcome.hit === true ? 'Hit' : outcome.hit === false ? 'Miss' : 'Undetermined'
}

function OutcomeCell({ outcome }: { outcome?: AgentPredictionOutcomeItem }) {
  if (!outcome || outcome.status === 'pending') return <span className="text-[12px] text-muted-foreground">{outcomeLabel(outcome)}</span>
  return <div className="text-right"><div className={`font-mono text-[12px] ${pctClass(outcome.return_pct)}`}>{formatPct(outcome.return_pct)}</div><div className={`text-[10px] ${outcome.hit ? 'text-up' : 'text-muted-foreground'}`}>{outcomeLabel(outcome)}</div></div>
}

function SummaryCard({ label, value, hint, tone = 'default' }: { label: string; value: string; hint?: string; tone?: 'default' | 'positive' | 'warning' }) {
  const valueClass = tone === 'positive' ? 'text-up' : tone === 'warning' ? 'text-amber-600' : 'text-foreground'
  return <div className="rounded-xl border border-border/60 bg-card/70 p-3.5"><div className="text-[11px] text-muted-foreground">{label}</div><div className={`mt-1 text-xl font-bold ${valueClass}`}>{value}</div>{hint && <div className="mt-1 text-[10px] text-muted-foreground">{hint}</div>}</div>
}

export default function EvaluationsPage() {
  const { toast } = useToast()
  const [searchParams] = useSearchParams()
  const [filters, setFilters] = useState<FilterState>(INITIAL_FILTERS)
  const [data, setData] = useState<AgentPredictionListResponse | null>(null)
  const [summary, setSummary] = useState<AgentPredictionSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [evaluating, setEvaluating] = useState(false)
  const [selected, setSelected] = useState<AgentPredictionGroup | null>(null)
  const targetGroupId = searchParams.get('prediction_group_id') || ''
  const apiFilters = useMemo(() => toApiFilters(filters), [filters])
  const rows = data?.items || []
  const options = data?.available_filters
  const policy = data?.policy || summary?.policy
  const oneDay = summary?.horizons['1']
  const fiveDay = summary?.horizons['5']

  const load = useCallback(async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [list, nextSummary] = await Promise.all([
        evaluationsApi.listAgentPredictions(apiFilters), evaluationsApi.getAgentPredictionSummary(apiFilters),
      ])
      setData(list)
      setSummary(nextSummary)
    } catch (error) {
      setLoadError(errorMessage(error, 'Failed to load evaluation data'))
    } finally {
      setLoading(false)
    }
  }, [apiFilters, toast])

  useEffect(() => { void load() }, [load])
  useEffect(() => {
    if (!targetGroupId || !data) return
    const target = data.items.find(item => item.prediction_group_id === targetGroupId)
    if (target) setSelected(target)
  }, [data, targetGroupId])

  const updateFilter = <K extends keyof FilterState>(key: K, value: FilterState[K]) => setFilters(current => ({ ...current, [key]: value }))
  const handleEvaluate = async () => {
    setEvaluating(true)
    try {
      const result = await evaluationsApi.evaluateAgentPredictions()
      toast(`Check done: ${result.evaluated} filled in, ${result.skipped_not_due} not yet due`, 'success')
      await load()
    } catch (error) {
      toast(error instanceof Error ? error.message : 'Failed to check items', 'error')
    } finally {
      setEvaluating(false)
    }
  }

  return <div className="w-full space-y-4 md:space-y-6">
    {loadError && <div className="card"><ErrorState title="Couldn't load evaluations" message={loadError} onRetry={() => void load()} className="py-6" /></div>}
    <section className="card overflow-hidden">
      <div className="p-4 md:p-5 border-b border-border/60">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="flex gap-3"><div className="w-10 h-10 shrink-0 rounded-xl bg-gradient-to-br from-violet-500 to-indigo-500 flex items-center justify-center shadow-sm"><ClipboardCheck className="w-5 h-5 text-white" /></div><div><div className="flex items-center gap-2 flex-wrap"><h1 className="text-lg md:text-xl font-bold">Evaluation centre</h1><Badge variant="secondary">Agent item review</Badge></div><p className="mt-1 text-[12px] md:text-[13px] text-muted-foreground">Compare past items with what the price actually did; TradingAgents' 1/5/20-day per-stock decisions stay on the analysis detail page.</p></div></div>
          <Button variant="outline" size="sm" onClick={() => void handleEvaluate()} disabled={evaluating}><RefreshCw className={`w-3.5 h-3.5 ${evaluating ? 'animate-spin' : ''}`} />{evaluating ? 'Checking' : 'Check due items'}</Button>
        </div>
      </div>
      <div className="p-4 md:p-5 space-y-4">
        <div className="grid grid-cols-2 lg:grid-cols-5 gap-3"><SummaryCard label="Items recorded" value={String(summary?.suggestion_count ?? '--')} hint="Deduplicated per item" /><SummaryCard label="Pending" value={String(summary?.pending_count ?? '--')} hint="Evaluation days not yet complete" tone="warning" /><SummaryCard label="1-trading-day hit rate" value={oneDay?.hit_rate != null ? `${(oneDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`Samples ${oneDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="5-trading-day hit rate" value={fiveDay?.hit_rate != null ? `${(fiveDay.hit_rate * 100).toFixed(0)}%` : '--'} hint={`Samples ${fiveDay?.completed_count ?? 0}`} tone="positive" /><SummaryCard label="5-day average return" value={formatPct(fiveDay?.avg_return_pct)} hint="Completed trading days only" /></div>
        {summary?.insufficient_sample && <div className="flex items-center gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-[11px] text-amber-700 dark:text-amber-300"><Target className="w-3.5 h-3.5 shrink-0" />Fewer than 20 completed 5-trading-day samples; the hit rate is for review only and isn't a stable conclusion yet.</div>}
        <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-8 gap-2 pt-1">
          <Select value={filters.agentName} onValueChange={value => updateFilter('agentName', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="All agents" /></SelectTrigger><SelectContent><SelectItem value="all">All agents</SelectItem>{options?.agent_names.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.market} onValueChange={value => updateFilter('market', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="All markets" /></SelectTrigger><SelectContent><SelectItem value="all">All markets</SelectItem>{options?.markets.map(value => <SelectItem key={value} value={value}>{value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.action} onValueChange={value => updateFilter('action', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="All actions" /></SelectTrigger><SelectContent><SelectItem value="all">All actions</SelectItem>{options?.actions.map(value => <SelectItem key={value} value={value}>{ACTION_LABELS[value] || value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.status} onValueChange={value => updateFilter('status', value)}><SelectTrigger className="h-8 text-[12px]"><SelectValue placeholder="All statuses" /></SelectTrigger><SelectContent><SelectItem value="all">All statuses</SelectItem>{options?.statuses.map(value => <SelectItem key={value} value={value}>{value === 'evaluated' ? 'Evaluated' : value === 'pending' ? 'Pending' : value}</SelectItem>)}</SelectContent></Select>
          <Select value={filters.horizonUnit} onValueChange={value => updateFilter('horizonUnit', value as EvaluationHorizonUnit)}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="trading_days">Trading days</SelectItem><SelectItem value="calendar_days_legacy">Old calendar days</SelectItem><SelectItem value="all">All bases</SelectItem></SelectContent></Select>
          <Select value={String(filters.days)} onValueChange={value => updateFilter('days', Number(value))}><SelectTrigger className="h-8 text-[12px]"><SelectValue /></SelectTrigger><SelectContent><SelectItem value="30">Last 30 days</SelectItem><SelectItem value="90">Last 90 days</SelectItem><SelectItem value="180">Last 180 days</SelectItem><SelectItem value="365">Last 365 days</SelectItem></SelectContent></Select>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-start">Start date</Label><Input id="evaluation-start" type="date" className="h-8 text-[11px]" value={filters.startDate} onChange={event => updateFilter('startDate', event.target.value)} /></div>
          <div className="col-span-1 flex items-center gap-1.5"><Label className="sr-only" htmlFor="evaluation-end">End date</Label><Input id="evaluation-end" type="date" className="h-8 text-[11px]" value={filters.endDate} onChange={event => updateFilter('endDate', event.target.value)} /></div>
        </div>
      </div>
    </section>
    <section className="card overflow-hidden"><div className="px-4 md:px-5 py-3 border-b border-border/60 flex items-center justify-between"><div className="text-[13px] font-semibold">Item details</div><div className="text-[11px] text-muted-foreground">{data?.total ?? 0} items</div></div>{loading ? <LoadingState rows={5} label="Loading the item review…" /> : rows.length === 0 ? <EmptyState title="No items match these filters" description="Try a wider date range or a different agent." /> : <div className="overflow-x-auto"><table className="w-full min-w-[860px] text-[12px]"><thead className="bg-accent/20 text-muted-foreground text-[11px]"><tr className="border-b border-border/50"><th className="py-2.5 px-4 text-left font-medium">Date</th><th className="py-2.5 px-2 text-left font-medium">Stock</th><th className="py-2.5 px-2 text-left font-medium">Source</th><th className="py-2.5 px-2 text-left font-medium">Action</th><th className="py-2.5 px-2 text-right font-medium">Confidence</th><th className="py-2.5 px-2 text-right font-medium">Price</th><th className="py-2.5 px-3 text-right font-medium">1 trading day</th><th className="py-2.5 px-4 text-right font-medium">5 trading days</th></tr></thead><tbody>{rows.map(row => <tr key={row.prediction_group_id} onClick={() => setSelected(row)} className="border-b border-border/40 cursor-pointer hover:bg-accent/30 transition-colors"><td className="py-3 px-4 font-mono text-muted-foreground">{row.prediction_date}</td><td className="py-3 px-2 font-medium">{row.stock_symbol}<span className="ml-1 text-[10px] text-muted-foreground">{row.stock_market}</span></td><td className="py-3 px-2 text-muted-foreground">{row.agent_name}</td><td className="py-3 px-2"><Badge variant="secondary" className="px-1.5 py-0.5">{row.action_label || ACTION_LABELS[row.action] || row.action}</Badge>{row.is_legacy_group && <span className="ml-1.5 text-[10px] text-amber-600">old basis</span>}</td><td className="py-3 px-2 text-right font-mono">{row.confidence == null ? '--' : row.confidence.toFixed(2)}</td><td className="py-3 px-2 text-right font-mono">{row.trigger_price == null ? '--' : row.trigger_price.toFixed(2)}</td><td className="py-3 px-3"><OutcomeCell outcome={row.outcomes['1']} /></td><td className="py-3 px-4"><OutcomeCell outcome={row.outcomes['5']} /></td></tr>)}</tbody></table></div>}</section>
    <Dialog open={!!selected} onOpenChange={open => !open && setSelected(null)}><DialogContent className="max-w-xl max-h-[80vh] overflow-y-auto"><DialogHeader><DialogTitle>{selected ? `${selected.stock_symbol} · ${selected.action_label || selected.action}` : 'Item outcome details'}</DialogTitle><DialogDescription>{selected?.prediction_date} · {selected?.agent_name} · {selected?.stock_market}</DialogDescription></DialogHeader>{selected && <div className="space-y-4 text-[13px]"><div className="grid grid-cols-3 gap-3 rounded-xl bg-accent/30 p-3"><div><div className="text-[10px] text-muted-foreground">Confidence</div><div className="mt-1 font-medium">{selected.confidence == null ? '--' : selected.confidence.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">Price</div><div className="mt-1 font-mono">{selected.trigger_price == null ? '--' : selected.trigger_price.toFixed(2)}</div></div><div><div className="text-[10px] text-muted-foreground">Basis</div><div className="mt-1 font-medium">{selected.is_legacy_group ? 'Old calendar days' : 'Trading days'}</div></div></div>{(selected.reason || selected.signal) && <div className="space-y-2"><div className="font-medium">Basis at the time</div>{selected.signal && <div className="rounded-lg border border-border/60 p-2.5 text-muted-foreground">Signal: {selected.signal}</div>}{selected.reason && <div className="rounded-lg border border-border/60 p-2.5 leading-relaxed text-muted-foreground">{selected.reason}</div>}</div>}<div className="space-y-2"><div className="font-medium">Outcome</div>{['1', '5'].map(horizon => { const outcome = selected.outcomes[horizon]; return <div key={horizon} className="flex items-center justify-between rounded-lg border border-border/60 p-3"><div className="flex items-center gap-2"><Clock3 className="w-3.5 h-3.5 text-muted-foreground" /><span>{horizon} trading days</span></div><div className="text-right"><div className={`font-mono ${pctClass(outcome?.return_pct)}`}>{outcome?.status === 'pending' ? 'Pending' : formatPct(outcome?.return_pct)}</div><div className="text-[10px] text-muted-foreground">{outcomeLabel(outcome)}</div></div></div> })}</div>{policy && <div className="rounded-lg bg-primary/5 p-3 text-[11px] text-muted-foreground"><div className="mb-1.5 flex items-center gap-1.5 font-medium text-foreground"><CheckCircle2 className="w-3.5 h-3.5 text-primary" />Hit rule</div>{policy.actions[selected.action] || `Watch-type items: a hit when the absolute return is under ${policy.flat_threshold_pct}%`}</div>}</div>}</DialogContent></Dialog>
  </div>
}
