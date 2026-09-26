import { useEffect, useMemo, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Plus, RefreshCw, Play, Trash2, BarChart3, BellRing } from 'lucide-react'
import { fetchAPI, stocksApi, type NotifyChannel } from '@candlewise/api'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@candlewise/base-ui/components/ui/dialog'
import { useToast } from '@candlewise/base-ui/components/ui/toast'
import PriceAlertFormDialog, { type AlertConditionItem, type PriceAlertFormState, type PriceAlertSubmitPayload } from '@candlewise/biz-ui/components/price-alert-form-dialog'
import { formatIST } from '@/lib/format'
import { PageHeader } from '@/components/common/Brand'
import { EmptyState, ErrorState, LoadingState, errorMessage } from '@/components/common/states'

type RuleOp = 'and' | 'or'

interface StockItem {
  id: number
  symbol: string
  name: string
  market: string
}

interface AlertRule {
  id: number
  stock_id: number
  stock_symbol: string
  stock_name: string
  market: string
  name: string
  enabled: boolean
  condition_group: {
    op: RuleOp
    items: AlertConditionItem[]
  }
  market_hours_mode: 'always' | 'trading_only'
  cooldown_minutes: number
  max_triggers_per_day: number
  repeat_mode: 'once' | 'repeat'
  expire_at: string | null
  notify_channel_ids: number[]
  last_trigger_at: string | null
  trigger_count_today: number
  trigger_date: string
}

interface AlertHit {
  id: number
  trigger_time: string
  trigger_snapshot: Record<string, any>
  notify_success: boolean
  notify_error: string
}

const DEFAULT_FORM: PriceAlertFormState = {
  stock_id: 0,
  name: '',
  op: 'and',
  items: [{ type: 'price', op: '>=', value: 0 }],
  market_hours_mode: 'trading_only',
  cooldown_minutes: 30,
  max_triggers_per_day: 3,
  repeat_mode: 'repeat',
  expire_at: '',
  notify_channel_ids: [],
}

function fmt(iso?: string | null): string {
  if (!iso) return '--'
  const d = new Date(iso)
  if (isNaN(d.getTime())) return '--'
  return formatIST(d, 'short')
}

function conditionText(item: AlertConditionItem): string {
  const TYPE_LABEL: Record<string, string> = {
    price: 'Price',
    change_pct: 'Change %',
    turnover: 'Turnover',
    volume: 'Volume',
    volume_ratio: 'Volume ratio',
  }
  if (item.op === 'between' && Array.isArray(item.value)) {
    return `${TYPE_LABEL[item.type] || item.type} ∈ [${item.value[0]}, ${item.value[1]}]`
  }
  return `${TYPE_LABEL[item.type] || item.type} ${item.op} ${Array.isArray(item.value) ? item.value.join('~') : item.value}`
}

export default function PriceAlertsPage() {
  const { toast } = useToast()
  const location = useLocation()
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [rules, setRules] = useState<AlertRule[]>([])
  const [stocks, setStocks] = useState<StockItem[]>([])
  const [channels, setChannels] = useState<NotifyChannel[]>([])
  const [formOpen, setFormOpen] = useState(false)
  const [saving, setSaving] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [form, setForm] = useState<PriceAlertFormState>(DEFAULT_FORM)
  const [hitsOpen, setHitsOpen] = useState(false)
  const [hitRule, setHitRule] = useState<AlertRule | null>(null)
  const [hits, setHits] = useState<AlertHit[]>([])
  const [scanRunning, setScanRunning] = useState(false)
  const [prefillDone, setPrefillDone] = useState(false)

  const stockOptions = useMemo(() => stocks, [stocks])

  const load = async () => {
    setLoading(true)
    setLoadError('')
    try {
      const [ruleData, stockData, channelData] = await Promise.all([
        fetchAPI<AlertRule[]>('/price-alerts'),
        stocksApi.list(),
        fetchAPI<NotifyChannel[]>('/channels'),
      ])
      setRules(ruleData || [])
      setStocks(stockData || [])
      setChannels(channelData || [])
    } catch (e) {
      setLoadError(errorMessage(e, 'Failed to load'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
  }, [])

  useEffect(() => {
    if (prefillDone) return
    if (loading) return
    const params = new URLSearchParams(location.search || '')
    if (!params.toString()) {
      setPrefillDone(true)
      return
    }
    const qStockId = Number(params.get('stock_id') || 0)
    const qSymbol = String(params.get('symbol') || '').trim().toUpperCase()
    const qMarket = String(params.get('market') || '').trim().toUpperCase() || 'IN'
    const qName = String(params.get('name') || '').trim()

    const openWithStock = async () => {
      let target = stocks.find(s => qStockId > 0 ? s.id === qStockId : (s.symbol === qSymbol && s.market === qMarket))
      if (!target && qSymbol) {
        try {
          target = await stocksApi.create({ symbol: qSymbol, market: qMarket, name: qName || qSymbol })
          if (target) setStocks(prev => prev.some(s => s.id === target!.id) ? prev : [target!, ...prev])
        } catch {
          // ignore and fallback to manual select
        }
      }
      setEditingId(null)
      setForm({
        ...DEFAULT_FORM,
        stock_id: target?.id || stockOptions[0]?.id || 0,
        name: target ? `${target.name} price alert` : (qName ? `${qName} price alert` : ''),
      })
      setFormOpen(true)
      setPrefillDone(true)
    }
    openWithStock()
  }, [loading, location.search, prefillDone, stockOptions, stocks])

  const openCreate = () => {
    setEditingId(null)
    setForm({
      ...DEFAULT_FORM,
      stock_id: stockOptions[0]?.id || 0,
    })
    setFormOpen(true)
  }

  const openEdit = (r: AlertRule) => {
    setEditingId(r.id)
    setForm({
      stock_id: r.stock_id,
      name: r.name || '',
      op: (r.condition_group?.op || 'and') as RuleOp,
      items: (r.condition_group?.items || [{ type: 'price', op: '>=', value: 0 }]) as AlertConditionItem[],
      market_hours_mode: (r.market_hours_mode || 'trading_only') as any,
      cooldown_minutes: r.cooldown_minutes ?? 30,
      max_triggers_per_day: r.max_triggers_per_day ?? 3,
      repeat_mode: (r.repeat_mode || 'repeat') as any,
      expire_at: r.expire_at ? r.expire_at.slice(0, 16) : '',
      notify_channel_ids: r.notify_channel_ids || [],
    })
    setFormOpen(true)
  }

  const submitForm = async (payloadInput?: PriceAlertSubmitPayload) => {
    const payload = payloadInput || {
      stock_id: form.stock_id,
      name: form.name.trim(),
      condition_group: { op: form.op, items: form.items },
      market_hours_mode: form.market_hours_mode,
      cooldown_minutes: Number(form.cooldown_minutes || 0),
      max_triggers_per_day: Number(form.max_triggers_per_day || 0),
      repeat_mode: form.repeat_mode,
      expire_at: form.expire_at ? new Date(form.expire_at).toISOString() : null,
      notify_channel_ids: form.notify_channel_ids || [],
    }
    if (!payload.stock_id) {
      toast('Please choose a stock', 'error')
      return
    }
    if (!payload.condition_group?.items?.length) {
      toast('Add at least one condition', 'error')
      return
    }
    setSaving(true)
    try {
      if (editingId) {
        await fetchAPI(`/price-alerts/${editingId}`, { method: 'PUT', body: JSON.stringify(payload) })
      } else {
        await fetchAPI('/price-alerts', { method: 'POST', body: JSON.stringify(payload) })
      }
      setFormOpen(false)
      await load()
      toast('Rule saved', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error')
    } finally {
      setSaving(false)
    }
  }

  const toggleRule = async (r: AlertRule) => {
    try {
      await fetchAPI(`/price-alerts/${r.id}/toggle`, { method: 'POST', body: JSON.stringify({ enabled: !r.enabled }) })
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Toggle failed', 'error')
    }
  }

  const removeRule = async (r: AlertRule) => {
    if (!window.confirm(`Delete the rule "${r.name || r.stock_name}"?`)) return
    try {
      await fetchAPI(`/price-alerts/${r.id}`, { method: 'DELETE' })
      await load()
      toast('Deleted', 'success')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Delete failed', 'error')
    }
  }

  const runScan = async () => {
    setScanRunning(true)
    try {
      const res = await fetchAPI<any>('/price-alerts/scan', { method: 'POST' })
      toast(`Scan done: triggered ${res?.triggered || 0}, skipped ${res?.skipped || 0}`, 'success')
      await load()
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Scan failed', 'error')
    } finally {
      setScanRunning(false)
    }
  }

  const testRule = async (r: AlertRule) => {
    try {
      const res = await fetchAPI<any>(`/price-alerts/${r.id}/test`, { method: 'POST' })
      const st = (res?.items || [])[0]?.status || 'unknown'
      toast(`Test done: ${st}`, 'info')
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Test failed', 'error')
    }
  }

  const openHits = async (r: AlertRule) => {
    setHitRule(r)
    setHitsOpen(true)
    try {
      const data = await fetchAPI<AlertHit[]>(`/price-alerts/${r.id}/hits?limit=50`)
      setHits(data || [])
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Failed to load triggers', 'error')
      setHits([])
    }
  }

  return (
    <div>
      <PageHeader
        eyebrow="Alerts"
        title="Price alerts"
        description="Triggered by price or volume, with cooldowns, daily limits and a market-hours gate. Alerts notify; they never place orders."
      />

      <div className="card p-4 mb-4 flex items-center justify-between gap-2">
        <div className="text-[12px] text-muted-foreground">Rules: {rules.length}</div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" className="h-8" onClick={runScan} disabled={scanRunning}>
            {scanRunning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            Scan now
          </Button>
          <Button size="sm" className="h-8" onClick={openCreate}>
            <Plus className="w-3.5 h-3.5" />
            New rule
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="card"><LoadingState rows={4} label="Loading alert rules…" /></div>
      ) : loadError ? (
        <div className="card"><ErrorState title="Couldn't load alerts" message={loadError} onRetry={load} /></div>
      ) : rules.length === 0 ? (
        <div className="card">
          <EmptyState
            icon={<BellRing className="h-5 w-5" />}
            title="No price alert rules"
            description="Once you create a rule, the app checks it every minute during market hours and notifies you when it triggers."
            action={<Button size="sm" onClick={openCreate}><Plus className="w-3.5 h-3.5" /> New rule</Button>}
          />
        </div>
      ) : (
        <div className="space-y-3">
          {rules.map(r => (
            <div key={r.id} className="card p-4">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-[14px] font-semibold">{r.name || `${r.stock_name} alert`}</span>
                    <span className="text-[11px] px-2 py-0.5 rounded bg-accent/50 text-muted-foreground">{r.market}:{r.stock_symbol}</span>
                    <span className={`text-[11px] px-2 py-0.5 rounded ${r.enabled ? 'bg-success/15 text-success' : 'bg-muted text-muted-foreground'}`}>{r.enabled ? 'On' : 'Paused'}</span>
                  </div>
                  <div className="mt-2 text-[12px] text-muted-foreground">
                    {(r.condition_group?.items || []).map(conditionText).join(r.condition_group?.op === 'or' ? ' or ' : ' and ')}
                  </div>
                  <div className="mt-1 text-[11px] text-muted-foreground/80">
                    Cooldown {r.cooldown_minutes} min · daily limit {r.max_triggers_per_day} · last triggered {fmt(r.last_trigger_at)}
                  </div>
                </div>
                {/* Desktop: buttons on the right */}
                <div className="hidden md:flex items-center gap-1.5 shrink-0">
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => testRule(r)}>Test</Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => openHits(r)}><BarChart3 className="w-3.5 h-3.5" /></Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => openEdit(r)}>Edit</Button>
                  <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-8 px-2.5" onClick={() => toggleRule(r)}>{r.enabled ? 'Disable' : 'Enable'}</Button>
                  <Button variant="secondary" size="sm" className="h-8 px-2.5" onClick={() => removeRule(r)}><Trash2 className="w-3.5 h-3.5" /></Button>
                </div>
              </div>
              {/* Mobile: buttons at bottom */}
              <div className="flex md:hidden items-center gap-1.5 mt-3 pt-3 border-t border-border/30">
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => testRule(r)}>Test</Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => openHits(r)}><BarChart3 className="w-3 h-3" /></Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => openEdit(r)}>Edit</Button>
                <div className="flex-1" />
                <Button variant={r.enabled ? 'destructive' : 'default'} size="sm" className="h-7 px-2 text-[11px]" onClick={() => toggleRule(r)}>{r.enabled ? 'Disable' : 'Enable'}</Button>
                <Button variant="secondary" size="sm" className="h-7 px-2 text-[11px]" onClick={() => removeRule(r)}><Trash2 className="w-3 h-3" /></Button>
              </div>
            </div>
          ))}
        </div>
      )}

      <PriceAlertFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        title={editingId ? 'Edit alert rule' : 'New alert rule'}
        description="Price, change %, turnover and volume ratio conditions, combined with AND / OR"
        stocks={stockOptions}
        channels={channels}
        initial={form}
        submitting={saving}
        submitLabel="Save rule"
        onSubmit={submitForm}
      />

      <Dialog open={hitsOpen} onOpenChange={setHitsOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Trigger history</DialogTitle>
            <DialogDescription>{hitRule?.name || '--'}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto scrollbar space-y-2">
            {hits.length === 0 ? (
              <div className="text-[12px] text-muted-foreground text-center py-6">No triggers yet</div>
            ) : hits.map(h => (
              <div key={h.id} className="rounded border border-border/40 p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[12px] text-muted-foreground">{fmt(h.trigger_time)}</div>
                  <div className={`text-[11px] ${h.notify_success ? 'text-success' : 'text-destructive'}`}>
                    {h.notify_success ? 'Notified' : `Notification failed ${h.notify_error || ''}`}
                  </div>
                </div>
                <div className="mt-2 text-[11px] bg-accent/20 rounded p-2 font-mono overflow-x-auto scrollbar">
                  {JSON.stringify(h.trigger_snapshot || {}, null, 2)}
                </div>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
