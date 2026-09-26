/**
 * TradingAgents deep research API.
 * Reuses the existing /api/stocks/:id/agents/:name/trigger with agent_name = "tradingagents".
 * Progress goes through the newer /api/agents/runs/:trace_id/progress.
 */
import { fetchAPI, getToken } from './client'

export interface TradingAgentsTriggerResult {
  ok: boolean
  queued?: boolean
  trace_id?: string
  message?: string
  /** Backend idempotency hit: a run is already in progress; trace_id belongs to that run, not a new one */
  deduplicated?: boolean
}

export interface AnalystReports {
  market: string
  social: string
  news: string
  fundamentals: string
}

export interface DebateHistory {
  history: string
  current_response: string
  judge_decision: string
}

export interface DeepAnalysisSuggestion {
  action: 'buy' | 'hold' | 'sell'
  action_label: string
  /** Upstream 5-level rating; review means it couldn't be parsed safely and needs manual review, not a plain hold. */
  rating_raw?: 'buy' | 'overweight' | 'hold' | 'underweight' | 'sell' | 'review'
  review_required?: boolean
  /** Upstream propagate's raw output, for display and for investigating mapping differences. */
  upstream_decision?: string
  signal: string
  reason: string
  should_alert: boolean
  agent_name: string
  agent_label: string
  confidence: number
}

export interface DeepAnalysisResult {
  agent_name: string
  title: string
  content: string
  raw_data: {
    /** "research_only" when the decision nodes were not run (ADR-005). */
    mode?: string
    /** Neutral research summary; replaces the decision in research-only mode. */
    research_summary?: string
    suggestion: DeepAnalysisSuggestion
    cost_usd: number
    should_alert: boolean
    decision: string
    upstream_decision?: string
    confidence: number
    debate_history: DebateHistory
    risk_judgment: string
    risk_debate?: { history: string; judge_decision: string }
    analyst_reports: AnalystReports
    final_decision: string
    trader_plan: string
    from_cache?: boolean
    notified?: boolean
    toolkit_diagnostic?: {
      summary: { hit: number; miss: number; passthrough: number; fallthrough?: number; error: number }
      recent: Array<{
        action?: string
        method?: string
        symbol?: string
        chars?: number
        snippet?: string
        source?: string
        reason?: string
      }>
    }
  }
  timestamp?: string
}

export interface ProgressStage {
  name: string
  status: 'pending' | 'running' | 'done'
  started_at?: string
  duration_sec?: number
  cost_usd?: number
}

export interface ProgressDataSource {
  name: string
  status: 'pending' | 'running' | 'done' | 'error'
  error?: string
}

export interface ProgressActiveOperation {
  kind: 'llm' | 'tool'
  name: string
  /** TradingAgents LangGraph node name; older backend snapshots may not have it. */
  agent?: string
}

export interface ToolkitHit {
  timestamp: string
  action: string  // HIT / MISS / PASSTHROUGH / ERROR
  method: string
  symbol: string
  reason?: string
  chars?: number
}

export interface ProgressResponse {
  trace_id: string
  status: 'not_found' | 'running' | 'success' | 'failed' | 'stale'
  current_stage?: string | null
  completed_stages: string[]
  started_at?: string | null
  elapsed_sec: number
  total_cost_usd: number
  active_operation?: ProgressActiveOperation | null
  stages: ProgressStage[]
  data_sources?: ProgressDataSource[]
  toolkit_summary?: { hit: number; miss: number; passthrough: number; fallthrough?: number; error: number }
  toolkit_recent?: ToolkitHit[]
  run?: {
    agent_name: string
    status: string
    result: string
    error: string
    duration_ms: number
    model_label: string
    notify_sent: boolean
  }
}

export interface BudgetInfo {
  used: number
  remaining: number
  limit: number
  exceeded: boolean
  runs_this_month: number
  estimate_next_run: {
    cost_low_usd: number
    cost_high_usd: number
    model: string
  }
  over_budget_action: 'reject' | 'warn' | 'continue'
  enabled: boolean
}

export interface HistoryComparisonItem {
  trace_id: string
  analysis_date: string
  action: 'buy' | 'hold' | 'sell'
  action_label: string
  confidence: number | null
  cost_usd: number | null
  price_at_analysis: number | null
  return_1d_pct: number | null
  return_5d_pct: number | null
  return_20d_pct: number | null
  hit_20d: boolean | null
}

export interface HistoryComparisonStats {
  total: number
  buy_count: number
  sell_count: number
  hold_count: number
  buy_hit_rate: number | null
  sell_hit_rate: number | null
  hold_hit_rate: number | null
  overall_hit_rate: number | null
  avg_return_20d_pct: number | null
}

export interface HistoryComparisonResponse {
  items: HistoryComparisonItem[]
  stats: HistoryComparisonStats
}

export const tradingAgentsApi = {
  /** Start deep research (queued asynchronously). force=true skips today's cache.
   *  TradingAgents doesn't need a StockAgent link; allow_unbound=true is always sent. */
  trigger(stockId: number, opts: { force?: boolean } = {}): Promise<TradingAgentsTriggerResult> {
    const qsParts = ['allow_unbound=true']
    if (opts.force) qsParts.push('force_refresh=true')
    return fetchAPI(
      `/stocks/${stockId}/agents/tradingagents/trigger?${qsParts.join('&')}`,
      {
        method: 'POST',
        body: JSON.stringify({}),
      },
    )
  },

  /** This month's budget + a per-run cost estimate (for the confirmation dialog before starting). */
  getBudget(): Promise<BudgetInfo> {
    return fetchAPI('/agents/tradingagents/budget')
  },

  /** Export a deep research report as a PDF and trigger the download (generated on the server, no print dialog). */
  async downloadAnalysisPdf(symbol: string, date: string): Promise<void> {
    const token = getToken()
    const qs = new URLSearchParams({ stock_symbol: symbol, analysis_date: date })
    const resp = await fetch(`/api/agents/tradingagents/analysis/pdf?${qs.toString()}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (!resp.ok) {
      let msg = `Export failed (${resp.status})`
      try {
        const j = await resp.json()
        msg = (j && (j.message || j.detail)) || msg
      } catch {
        /* Not a JSON error body; use the default message */
      }
      throw new Error(msg)
    }
    const blob = await resp.blob()
    let filename = `deep-research-${date}.pdf`
    const cd = resp.headers.get('Content-Disposition') || ''
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i)
    if (m) {
      try {
        filename = decodeURIComponent(m[1])
      } catch {
        /* keep the default file name */
      }
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  },

  /** Is there a TA run in progress or just finished for this stock in the last 30 minutes (the backend is the authority)?
   *  Returns status: running | success | failed | stale | none
   *  stale = no new progress logs for 5 minutes; the frontend can reset to idle and allow a new trigger */
  findRunning(symbol: string): Promise<{
    trace_id: string | null
    status: 'running' | 'success' | 'failed' | 'stale' | 'none'
    last_activity_at?: string
  }> {
    return fetchAPI(`/agents/tradingagents/running?stock_symbol=${encodeURIComponent(symbol)}`)
  },

  /** Fetch progress (frontend polling). */
  getProgress(traceId: string): Promise<ProgressResponse> {
    return fetchAPI(`/agents/runs/${encodeURIComponent(traceId)}/progress`)
  },

  /** Past decisions vs actual moves. */
  getHistoryComparison(
    symbol: string,
    market: string,
    days = 90,
  ): Promise<HistoryComparisonResponse> {
    const qs = new URLSearchParams({
      stock_symbol: symbol,
      market,
      days: String(days),
    })
    return fetchAPI(`/agents/tradingagents/history-comparison?${qs.toString()}`)
  },

  /** Fetch a stock's latest deep research result (with the full raw_data). */
  getLatestForStock(symbol: string): Promise<DeepAnalysisResult | null> {
    return fetchAPI(
      `/agents/tradingagents/latest?stock_symbol=${encodeURIComponent(symbol)}`,
    ).then((item: unknown) => {
      if (!item || typeof item !== 'object') return null
      const rec = item as { content?: string; title?: string; raw_data?: unknown; analysis_date?: string }
      if (!rec.content) return null
      return {
        agent_name: 'tradingagents',
        title: rec.title || '',
        content: rec.content || '',
        raw_data: (rec.raw_data || {}) as DeepAnalysisResult['raw_data'],
        timestamp: rec.analysis_date,
      }
    })
  },

  /** Fetch one deep research result by symbol + date (for the detail reading page). */
  getAnalysisByDate(symbol: string, date: string): Promise<DeepAnalysisResult | null> {
    const qs = new URLSearchParams({ stock_symbol: symbol, analysis_date: date })
    return fetchAPI(`/agents/tradingagents/analysis?${qs.toString()}`).then((item: unknown) => {
      if (!item || typeof item !== 'object') return null
      const rec = item as { content?: string; title?: string; raw_data?: unknown; analysis_date?: string }
      if (!rec.content) return null
      return {
        agent_name: 'tradingagents',
        title: rec.title || '',
        content: rec.content || '',
        raw_data: (rec.raw_data || {}) as DeepAnalysisResult['raw_data'],
        timestamp: rec.analysis_date,
      }
    })
  },
}
