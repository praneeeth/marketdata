import { useEffect, useState, useCallback } from 'react'
import { RefreshCw, Power, RotateCcw, X, TrendingUp, TrendingDown, Trophy, BarChart3, Wallet, Activity, Play, Bell, SlidersHorizontal } from 'lucide-react'
import {
  paperTradingApi,
  type PaperTradingAccountResponse,
  type PaperTradingPositionItem,
  type PaperTradingTradeItem,
  type EquityCurvePoint,
  type StrategyPerformanceItem,
  type NotifyChannelItem,
  type MarketView,
} from '@candlewise/api'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { Switch } from '@candlewise/base-ui/components/ui/switch'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@candlewise/base-ui/components/ui/dialog'
import { useToast } from '@candlewise/base-ui/components/ui/toast'
import { useCompliance } from '@/hooks/use-compliance'

const EXIT_REASON_MAP: Record<string, string> = {
  stop_loss: 'Stop loss',
  target_price: 'Take profit',
  signal_reversal: 'Signal reversal',
  manual: 'Manual close',
}

function formatCurrency(v: number) {
  return v.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function PnlText({ value, suffix = '' }: { value: number; suffix?: string }) {
  const color = value > 0 ? 'text-emerald-500' : value < 0 ? 'text-rose-500' : 'text-muted-foreground'
  const prefix = value > 0 ? '+' : ''
  return <span className={color}>{prefix}{formatCurrency(value)}{suffix}</span>
}

function PnlPctText({ value }: { value: number }) {
  const color = value > 0 ? 'text-emerald-500' : value < 0 ? 'text-rose-500' : 'text-muted-foreground'
  const prefix = value > 0 ? '+' : ''
  return <span className={color}>{prefix}{value.toFixed(2)}%</span>
}

function EquityChart({ data }: { data: EquityCurvePoint[] }) {
  if (data.length < 2) {
    return <div className="h-48 flex items-center justify-center text-muted-foreground text-sm">Not enough data to draw the curve</div>
  }

  const width = 600
  const height = 180
  const pad = { top: 20, right: 20, bottom: 30, left: 60 }
  const w = width - pad.left - pad.right
  const h = height - pad.top - pad.bottom

  const values = data.map(d => d.equity)
  const minV = Math.min(...values)
  const maxV = Math.max(...values)
  const range = maxV - minV || 1

  const points = data.map((d, i) => {
    const x = pad.left + (i / (data.length - 1)) * w
    const y = pad.top + h - ((d.equity - minV) / range) * h
    return { x, y, ...d }
  })

  const pathD = points.map((p, i) => `${i === 0 ? 'M' : 'L'}${p.x},${p.y}`).join(' ')
  const areaD = pathD + ` L${points[points.length - 1].x},${pad.top + h} L${points[0].x},${pad.top + h} Z`

  const isPositive = values[values.length - 1] >= values[0]
  const strokeColor = isPositive ? '#10b981' : '#ef4444'
  const fillColor = isPositive ? 'rgba(244,63,94,0.1)' : 'rgba(16,185,129,0.1)'

  // Y axis ticks
  const yTicks = 4
  const yLabels = Array.from({ length: yTicks + 1 }, (_, i) => {
    const v = minV + (range / yTicks) * i
    return { v, y: pad.top + h - (i / yTicks) * h }
  })

  // X axis labels (show first, middle, last)
  const xIndices = [0, Math.floor(data.length / 2), data.length - 1]
  const xLabels = xIndices.map(i => ({ label: data[i].date.slice(5), x: points[i].x }))

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto" preserveAspectRatio="xMidYMid meet">
      {/* Grid lines */}
      {yLabels.map((t, i) => (
        <g key={i}>
          <line x1={pad.left} x2={width - pad.right} y1={t.y} y2={t.y} stroke="hsl(var(--border))" strokeWidth={0.5} />
          <text x={pad.left - 6} y={t.y + 4} textAnchor="end" fill="hsl(var(--muted-foreground))" fontSize={10}>
            {(t.v / 100000).toFixed(1)}L
          </text>
        </g>
      ))}
      {/* Area */}
      <path d={areaD} fill={fillColor} />
      {/* Line */}
      <path d={pathD} fill="none" stroke={strokeColor} strokeWidth={2} />
      {/* X labels */}
      {xLabels.map((l, i) => (
        <text key={i} x={l.x} y={height - 6} textAnchor="middle" fill="hsl(var(--muted-foreground))" fontSize={10}>
          {l.label}
        </text>
      ))}
    </svg>
  )
}

export default function PaperTradingPage() {
  const { toast } = useToast()
  const { isEnabled, status: compliance } = useCompliance()
  const aiTradingEnabled = isEnabled('ai_paper_trading')
  const [account, setAccount] = useState<PaperTradingAccountResponse | null>(null)
  const [positions, setPositions] = useState<PaperTradingPositionItem[]>([])
  const [trades, setTrades] = useState<PaperTradingTradeItem[]>([])
  const [tradesTotal, setTradesTotal] = useState(0)
  const [equityCurve, setEquityCurve] = useState<EquityCurvePoint[]>([])
  const [strategyPerf, setStrategyPerf] = useState<StrategyPerformanceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const [tradesPage, setTradesPage] = useState(0)
  const tradesPageSize = 20

  // Market view (single-choice segments; switching refreshes the stats for that market)
  const [marketView, setMarketView] = useState<MarketView>('ALL')

  // Capital allocation
  const [configOpen, setConfigOpen] = useState(false)
  const [cfgTotal, setCfgTotal] = useState('')
  const [cfgRatios, setCfgRatios] = useState<{ IN: string }>({ IN: '' })
  const [cfgSaving, setCfgSaving] = useState(false)

  // Notification settings
  const [tradesOpen, setTradesOpen] = useState(false)
  const [notifyOpen, setNotifyOpen] = useState(false)
  const [notifyEnabled, setNotifyEnabled] = useState(false)
  const [notifyRealtime, setNotifyRealtime] = useState(true)
  const [notifyPremarket, setNotifyPremarket] = useState(true)
  const [notifySummary, setNotifySummary] = useState(true)
  const [notifyChannels, setNotifyChannels] = useState<NotifyChannelItem[]>([])
  const [selectedChannelIds, setSelectedChannelIds] = useState<Set<number>>(new Set())
  const [notifySaving, setNotifySaving] = useState(false)
  const [notifyTesting, setNotifyTesting] = useState(false)

  const loadData = useCallback(async () => {
    setLoading(true)
    try {
      const mkt = marketView === 'ALL' ? undefined : marketView
      const [acc, pos, tradeData, metrics] = await Promise.all([
        paperTradingApi.getAccount(mkt),
        paperTradingApi.listPositions('open', mkt),
        paperTradingApi.listTrades(tradesPageSize, tradesPage * tradesPageSize, mkt),
        paperTradingApi.getMetrics(mkt),
      ])
      setAccount(acc)
      setPositions(pos)
      setTrades(tradeData.items)
      setTradesTotal(tradeData.total)
      setEquityCurve(metrics.equity_curve)
      setStrategyPerf(metrics.strategy_performance || [])
    } catch {
      toast('Failed to load', 'error')
    } finally {
      setLoading(false)
    }
  }, [tradesPage, marketView])

  useEffect(() => { loadData() }, [loadData])

  const handleToggle = async () => {
    if (!account) return
    try {
      const res = await paperTradingApi.toggleAccount(!account.enabled)
      setAccount(res)
      toast(res.enabled ? 'Simulation started' : 'Simulation paused', 'success')
    } catch {
      toast('Action failed', 'error')
    }
  }

  const handleReset = async () => {
    if (!confirm('Reset the simulation? All positions and trade records will be cleared.')) return
    try {
      await paperTradingApi.resetAccount()
      toast('Simulation reset', 'success')
      loadData()
    } catch {
      toast('Reset failed', 'error')
    }
  }

  const handleScan = async () => {
    setScanning(true)
    try {
      const res = await paperTradingApi.scan()
      toast(`Scan done: opened ${res.opened ?? 0}, closed ${res.closed ?? 0}`, 'success')
      loadData()
    } catch {
      toast('Scan failed', 'error')
    } finally {
      setScanning(false)
    }
  }

  const handleClosePosition = async (id: number) => {
    try {
      await paperTradingApi.closePosition(id)
      toast('Position closed', 'success')
      loadData()
    } catch {
      toast('Close failed', 'error')
    }
  }

  const handleOpenConfig = async () => {
    setConfigOpen(true)
    try {
      // Total capital and per-market ratios on the "All" basis
      const acc = await paperTradingApi.getAccount()
      setCfgTotal(String(Math.round(acc.initial_capital)))
      const a = acc.market_allocations || {}
      setCfgRatios({ IN: String(Math.round((a.IN ?? 0) * 100)) })
    } catch {
      toast('Failed to load the config', 'error')
    }
  }

  const handleSaveConfig = async () => {
    const total = Number(cfgTotal)
    const inr = Number(cfgRatios.IN) || 0
    if (!(total > 0)) {
      toast('Total capital must be greater than 0', 'error')
      return
    }
    if (inr > 100) {
      toast("Ratios can't add up to more than 100%", 'error')
      return
    }
    setCfgSaving(true)
    try {
      await paperTradingApi.updateSettings({
        initial_capital: total,
        market_allocations: { IN: inr / 100 },
      })
      toast('Capital allocation saved', 'success')
      setConfigOpen(false)
      loadData()
    } catch {
      toast('Save failed', 'error')
    } finally {
      setCfgSaving(false)
    }
  }

  const loadNotifySettings = async () => {
    try {
      const data = await paperTradingApi.getNotifySettings()
      const s = data.settings
      setNotifyEnabled(s.pt_notify_enabled === 'true')
      setNotifyRealtime(s.pt_notify_realtime === 'true')
      setNotifyPremarket(s.pt_notify_premarket === 'true')
      setNotifySummary(s.pt_notify_summary === 'true')
      setNotifyChannels(data.channels)
      const ids = s.pt_notify_channel_ids
        ? new Set(s.pt_notify_channel_ids.split(',').map(Number).filter(Boolean))
        : new Set<number>()
      setSelectedChannelIds(ids)
    } catch {
      toast('Failed to load the notification config', 'error')
    }
  }

  const handleOpenNotify = async () => {
    setNotifyOpen(true)
    await loadNotifySettings()
  }

  const handleSaveNotify = async () => {
    setNotifySaving(true)
    try {
      await paperTradingApi.updateNotifySettings({
        pt_notify_enabled: notifyEnabled ? 'true' : 'false',
        pt_notify_channel_ids: Array.from(selectedChannelIds).join(','),
        pt_notify_realtime: notifyRealtime ? 'true' : 'false',
        pt_notify_premarket: notifyPremarket ? 'true' : 'false',
        pt_notify_summary: notifySummary ? 'true' : 'false',
      })
      toast('Notification config saved', 'success')
      setNotifyOpen(false)
    } catch {
      toast('Save failed', 'error')
    } finally {
      setNotifySaving(false)
    }
  }

  const handleTestNotify = async () => {
    setNotifyTesting(true)
    try {
      await paperTradingApi.testNotify()
      toast('Test notification sent', 'success')
    } catch {
      toast('Test notification failed', 'error')
    } finally {
      setNotifyTesting(false)
    }
  }

  const toggleChannel = (id: number) => {
    setSelectedChannelIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const totalPages = Math.ceil(tradesTotal / tradesPageSize)
  const ratioSum = Number(cfgRatios.IN) || 0

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shrink-0">
            <Activity className="w-4 h-4 text-white" />
          </div>
          <h1 className="text-lg font-bold">Simulation</h1>
          <span
            className="text-xs px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-600 font-semibold"
            data-testid="simulation-label"
          >
            {compliance?.simulation.label || 'Simulation'}
          </span>
          {aiTradingEnabled && account && (
            <span className={`text-xs px-2 py-0.5 rounded-full ${account.enabled ? 'bg-success/10 text-success' : 'bg-muted text-muted-foreground'}`}>
              {account.enabled ? 'Running' : 'Paused'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {tradesTotal > 0 && (
            <Button variant="outline" size="sm" className="h-8" onClick={() => setTradesOpen(true)}>
              <BarChart3 className="w-3.5 h-3.5" />
              <span className="hidden sm:inline ml-1">Closed trades ({tradesTotal})</span>
              <span className="sm:hidden ml-1">{tradesTotal}</span>
            </Button>
          )}
          {aiTradingEnabled && (
            <Button variant="outline" size="sm" className="h-8" onClick={handleOpenNotify}>
              <Bell className="w-3.5 h-3.5" />
              <span className="hidden sm:inline ml-1">Notifications</span>
            </Button>
          )}
          {aiTradingEnabled && (
            <Button variant="outline" size="sm" className="h-8" onClick={handleScan} disabled={scanning}>
              <Play className="w-3.5 h-3.5 mr-1" />
              <span className="hidden sm:inline">{scanning ? 'Scanning...' : 'Scan now'}</span>
              <span className="sm:hidden">{scanning ? 'Scanning' : 'Scan'}</span>
            </Button>
          )}
          <Button variant="outline" size="sm" className="h-8" onClick={loadData} disabled={loading}>
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            <span className="hidden sm:inline ml-1">Refresh</span>
          </Button>
          {aiTradingEnabled && (
            <Button variant="outline" size="sm" className="h-8" onClick={handleToggle}>
              <Power className="w-3.5 h-3.5" />
              <span className="hidden sm:inline ml-1">{account?.enabled ? 'Pause' : 'Start'}</span>
            </Button>
          )}
          <Button variant="outline" size="sm" className="h-8 text-destructive hover:text-destructive" onClick={handleReset}>
            <RotateCcw className="w-3.5 h-3.5" />
            <span className="hidden sm:inline ml-1">Reset</span>
          </Button>
        </div>
      </div>

      <div
        className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-[12px] text-foreground"
        data-testid="simulation-notice"
      >
        {compliance?.simulation.notice ||
          'Simulated trades only. No real orders are placed and no money is at risk.'}
        {!aiTradingEnabled &&
          ' AI-generated simulated trades are turned off in research-only mode; past simulation history remains visible.'}
      </div>

      {/* Market View Filter + capital allocation */}
      {account && (
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground text-xs">Market:</span>
            {(['ALL', 'IN'] as const).map(m => {
              const label = m === 'ALL' ? 'All' : 'NSE/BSE'
              const active = marketView === m
              const ratio = m !== 'ALL' ? account.market_allocations?.[m] : undefined
              const isOff = m !== 'ALL' && (ratio ?? 0) <= 0
              return (
                <button
                  key={m}
                  onClick={() => setMarketView(m)}
                  className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                    active
                      ? 'bg-primary text-primary-foreground'
                      : isOff
                      ? 'bg-muted/50 text-muted-foreground'
                      : 'bg-primary/10 text-primary ring-1 ring-primary/20'
                  }`}
                >
                  {label}{m !== 'ALL' && ratio != null ? ` ${Math.round(ratio * 100)}%` : ''}
                </button>
              )
            })}
          </div>
          <Button variant="outline" size="sm" className="h-8" onClick={handleOpenConfig}>
            <SlidersHorizontal className="w-3.5 h-3.5" />
            <span className="hidden sm:inline ml-1">Capital allocation</span>
          </Button>
        </div>
      )}

      {/* Summary Cards */}
      {account && (
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          <div className="card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
              <Wallet className="w-3.5 h-3.5" />
              Total assets
            </div>
            <div className="text-lg font-bold">{formatCurrency(account.total_equity)}</div>
          </div>
          <div className="card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
              {account.total_pnl >= 0 ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
              Total return
            </div>
            <div className="text-lg font-bold"><PnlText value={account.total_pnl} /></div>
          </div>
          <div className="card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
              <Trophy className="w-3.5 h-3.5" />
              Win rate
            </div>
            <div className="text-lg font-bold">{account.win_rate.toFixed(1)}%</div>
            <div className="text-xs text-muted-foreground">{account.winning_trades}/{account.total_trades} trades</div>
          </div>
          <div className="card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
              <BarChart3 className="w-3.5 h-3.5" />
              Max drawdown
            </div>
            <div className="text-lg font-bold text-emerald-500">{account.max_drawdown_pct.toFixed(2)}%</div>
          </div>
          <div className="card p-3">
            <div className="flex items-center gap-1.5 text-muted-foreground text-xs mb-1">
              <Wallet className="w-3.5 h-3.5" />
              Available cash
            </div>
            <div className="text-lg font-bold">{formatCurrency(account.current_capital)}</div>
          </div>
        </div>
      )}

      {/* Equity Curve */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold mb-3">Return curve</h2>
        <EquityChart data={equityCurve} />
      </div>

      {/* Strategy Performance */}
      {strategyPerf.length > 0 && (
        <div className="card p-4">
          <h2 className="text-sm font-semibold mb-3">Strategy performance</h2>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground text-xs">
                  <th className="text-left py-2 pr-3">Strategy</th>
                  <th className="text-right py-2 px-2">Closed</th>
                  <th className="text-right py-2 px-2">Win rate</th>
                  <th className="text-right py-2 px-2">Realised P&amp;L</th>
                  <th className="text-right py-2 px-2">Avg P&amp;L %</th>
                  <th className="text-right py-2 px-2">Avg days held</th>
                  <th className="text-right py-2 px-2">Open</th>
                  <th className="text-right py-2 pl-2">Unrealised P&amp;L</th>
                </tr>
              </thead>
              <tbody>
                {strategyPerf.map(s => (
                  <tr key={s.strategy_code} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-2 pr-3 font-medium">{s.strategy_code}</td>
                    <td className="text-right py-2 px-2">{s.total_trades}</td>
                    <td className="text-right py-2 px-2">
                      {s.total_trades > 0 ? (
                        <span className={s.win_rate >= 50 ? 'text-emerald-500' : s.win_rate > 0 ? 'text-amber-500' : 'text-muted-foreground'}>
                          {s.win_rate.toFixed(1)}%
                        </span>
                      ) : '-'}
                    </td>
                    <td className="text-right py-2 px-2"><PnlText value={s.total_pnl} /></td>
                    <td className="text-right py-2 px-2"><PnlPctText value={s.avg_pnl_pct} /></td>
                    <td className="text-right py-2 px-2">{s.total_trades > 0 ? `${s.avg_holding_days}d` : '-'}</td>
                    <td className="text-right py-2 px-2">{s.open_positions > 0 ? s.open_positions : '-'}</td>
                    <td className="text-right py-2 pl-2">
                      {s.open_positions > 0 ? <PnlText value={s.unrealized_pnl} /> : '-'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Open Positions */}
      <div className="card p-4">
        <h2 className="text-sm font-semibold mb-3">Open positions ({positions.length})</h2>
        {positions.length === 0 ? (
          <div className="text-center text-muted-foreground text-sm py-8">No positions</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-muted-foreground text-xs">
                  <th className="text-left py-2 pr-3">Stock</th>
                  <th className="text-right py-2 px-2">Entry</th>
                  <th className="text-right py-2 px-2">Price</th>
                  <th className="text-right py-2 px-2">Unrealised P&amp;L</th>
                  <th className="text-right py-2 px-2">Stop</th>
                  <th className="text-right py-2 px-2">Target</th>
                  <th className="text-left py-2 px-2">Strategy</th>
                  <th className="text-right py-2 px-2">Days held</th>
                  <th className="text-right py-2 pl-2">Actions</th>
                </tr>
              </thead>
              <tbody>
                {positions.map(p => (
                  <tr key={p.id} className="border-b border-border/50 hover:bg-accent/30">
                    <td className="py-2 pr-3">
                      <div className="font-medium">{p.stock_name || p.stock_symbol}</div>
                      <div className="text-xs text-muted-foreground">{p.stock_symbol} · {p.stock_market}</div>
                    </td>
                    <td className="text-right py-2 px-2">{p.entry_price.toFixed(2)}</td>
                    <td className="text-right py-2 px-2">{p.current_price?.toFixed(2) ?? '-'}</td>
                    <td className="text-right py-2 px-2">
                      <PnlText value={p.unrealized_pnl} />
                      <div className="text-xs"><PnlPctText value={p.unrealized_pnl_pct} /></div>
                    </td>
                    <td className="text-right py-2 px-2">{p.stop_loss?.toFixed(2) ?? '-'}</td>
                    <td className="text-right py-2 px-2">{p.target_price?.toFixed(2) ?? '-'}</td>
                    <td className="py-2 px-2 text-xs text-muted-foreground">{p.strategy_code || '-'}</td>
                    <td className="text-right py-2 px-2">{p.holding_days}d</td>
                    <td className="text-right py-2 pl-2">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-7 px-2 text-destructive hover:text-destructive"
                        onClick={() => handleClosePosition(p.id)}
                      >
                        <X className="w-3.5 h-3.5 mr-0.5" />
                        Close
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Trade History Dialog */}
      <Dialog open={tradesOpen} onOpenChange={setTradesOpen}>
        <DialogContent className="max-w-4xl max-h-[80vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Closed trades ({tradesTotal})</DialogTitle>
            <DialogDescription>Trade history</DialogDescription>
          </DialogHeader>
          {trades.length === 0 ? (
            <div className="text-center text-muted-foreground text-sm py-8">No trades</div>
          ) : (
            <>
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-border text-muted-foreground text-xs">
                      <th className="text-left py-2 pr-3">Stock</th>
                      <th className="text-right py-2 px-2">Entry</th>
                      <th className="text-right py-2 px-2">Exit</th>
                      <th className="text-right py-2 px-2">P&amp;L</th>
                      <th className="text-right py-2 px-2">P&amp;L %</th>
                      <th className="text-left py-2 px-2">Exit reason</th>
                      <th className="text-left py-2 px-2">Strategy</th>
                      <th className="text-right py-2 px-2">Days held</th>
                      <th className="text-right py-2 pl-2">Closed at</th>
                    </tr>
                  </thead>
                  <tbody>
                    {trades.map(t => (
                      <tr key={t.id} className="border-b border-border/50 hover:bg-accent/30">
                        <td className="py-2 pr-3">
                          <div className="font-medium">{t.stock_name || t.stock_symbol}</div>
                          <div className="text-xs text-muted-foreground">{t.stock_symbol} · {t.stock_market}</div>
                        </td>
                        <td className="text-right py-2 px-2">{t.entry_price.toFixed(2)}</td>
                        <td className="text-right py-2 px-2">{t.exit_price.toFixed(2)}</td>
                        <td className="text-right py-2 px-2"><PnlText value={t.pnl} /></td>
                        <td className="text-right py-2 px-2"><PnlPctText value={t.pnl_pct} /></td>
                        <td className="py-2 px-2 text-xs">{EXIT_REASON_MAP[t.exit_reason] || t.exit_reason}</td>
                        <td className="py-2 px-2 text-xs text-muted-foreground">{t.strategy_code || '-'}</td>
                        <td className="text-right py-2 px-2">{t.holding_days}d</td>
                        <td className="text-right py-2 pl-2 text-xs text-muted-foreground">{t.closed_at?.slice(0, 10) || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {totalPages > 1 && (
                <div className="flex items-center justify-center gap-2 mt-3">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={tradesPage === 0}
                    onClick={() => setTradesPage(p => Math.max(0, p - 1))}
                  >
                    Previous
                  </Button>
                  <span className="text-xs text-muted-foreground">
                    {tradesPage + 1} / {totalPages}
                  </span>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={tradesPage >= totalPages - 1}
                    onClick={() => setTradesPage(p => p + 1)}
                  >
                    Next
                  </Button>
                </div>
              )}
            </>
          )}
        </DialogContent>
      </Dialog>

      {/* Capital allocation dialog */}
      <Dialog open={configOpen} onOpenChange={setConfigOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Capital allocation</DialogTitle>
            <DialogDescription>Set the total capital and each market's ratio; a ratio of 0 means no money goes to that market (existing positions stay; only new entries stop)</DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div>
              <div className="text-sm font-medium mb-1">Total capital</div>
              <input
                type="number"
                value={cfgTotal}
                onChange={e => setCfgTotal(e.target.value)}
                className="w-full h-9 px-3 rounded-lg border border-border bg-background text-sm"
                placeholder="e.g. 1000000"
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between text-sm font-medium">
                <span>Ratio per market</span>
                <span className={`text-xs ${ratioSum > 100 ? 'text-destructive' : 'text-muted-foreground'}`}>
                  Total {ratioSum}%{ratioSum > 100 ? ' (over 100%)' : ''}
                </span>
              </div>
              {(['IN'] as const).map(m => {
                const label = 'NSE/BSE'
                const pct = Number(cfgRatios[m]) || 0
                const amount = ((Number(cfgTotal) || 0) * pct) / 100
                return (
                  <div key={m} className="flex items-center gap-3">
                    <span className="w-12 text-sm">{label}</span>
                    <input
                      type="number"
                      min={0}
                      max={100}
                      value={cfgRatios[m]}
                      onChange={e => setCfgRatios(prev => ({ ...prev, [m]: e.target.value }))}
                      className="w-20 h-9 px-2 rounded-lg border border-border bg-background text-sm text-right"
                    />
                    <span className="text-sm text-muted-foreground">%</span>
                    <span className="text-xs text-muted-foreground ml-auto">≈ {formatCurrency(amount)}</span>
                  </div>
                )
              })}
              <div className="text-xs text-muted-foreground">The total can be under 100%; the rest stays uninvested.</div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Button size="sm" onClick={handleSaveConfig} disabled={cfgSaving || ratioSum > 100}>
                {cfgSaving ? 'Saving...' : 'Save'}
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      {/* Simulation notification settings dialog */}
      <Dialog open={notifyOpen} onOpenChange={setNotifyOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Simulation notifications</DialogTitle>
            <DialogDescription>Notifications for simulated trades, tracking entries and exits as they happen</DialogDescription>
          </DialogHeader>

          <div className="space-y-5">
            {/* Master switch */}
            <div className="flex items-center justify-between">
              <div>
                <div className="text-sm font-medium">Enable notifications</div>
                <div className="text-xs text-muted-foreground">When on, trade notifications go to the chosen channels</div>
              </div>
              <Switch checked={notifyEnabled} onCheckedChange={setNotifyEnabled} />
            </div>

            {notifyEnabled && (
              <>
                {/* Channel choice */}
                <div>
                  <div className="text-sm font-medium mb-2">Notification channels</div>
                  {notifyChannels.length === 0 ? (
                    <div className="text-xs text-muted-foreground">No channels available; set one up in Settings first</div>
                  ) : (
                    <div className="flex flex-wrap gap-2">
                      {notifyChannels.map(ch => (
                        <button
                          key={ch.id}
                          onClick={() => toggleChannel(ch.id)}
                          className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-all ${
                            selectedChannelIds.has(ch.id)
                              ? 'bg-primary/10 text-primary ring-1 ring-primary/20'
                              : 'bg-muted/50 text-muted-foreground'
                          }`}
                        >
                          {ch.name}
                        </button>
                      ))}
                    </div>
                  )}
                  {selectedChannelIds.size === 0 && notifyChannels.length > 0 && (
                    <div className="text-xs text-muted-foreground mt-1">The default channel is used when none is chosen</div>
                  )}
                </div>

                {/* Notification types */}
                <div className="space-y-3">
                  <div className="text-sm font-medium">Notification types</div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">Live trade signals</div>
                      <div className="text-xs text-muted-foreground">Sent at once on entry/exit</div>
                    </div>
                    <Switch checked={notifyRealtime} onCheckedChange={setNotifyRealtime} />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">Pre-market plan</div>
                      <div className="text-xs text-muted-foreground">Today's candidates, every day at 09:00</div>
                    </div>
                    <Switch checked={notifyPremarket} onCheckedChange={setNotifyPremarket} />
                  </div>
                  <div className="flex items-center justify-between">
                    <div>
                      <div className="text-sm">End-of-day summary</div>
                      <div className="text-xs text-muted-foreground">Today's activity, every day at 15:30</div>
                    </div>
                    <Switch checked={notifySummary} onCheckedChange={setNotifySummary} />
                  </div>
                </div>
              </>
            )}

            {/* Action buttons */}
            <div className="flex items-center gap-2 pt-2">
              <Button size="sm" onClick={handleSaveNotify} disabled={notifySaving}>
                {notifySaving ? 'Saving...' : 'Save'}
              </Button>
              {notifyEnabled && (
                <Button variant="outline" size="sm" onClick={handleTestNotify} disabled={notifyTesting}>
                  {notifyTesting ? 'Sending...' : 'Test notification'}
                </Button>
              )}
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  )
}
