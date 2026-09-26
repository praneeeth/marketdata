/**
 * Deep research dialog (TradingAgents).
 *
 * Three states:
 * 1. Starting: shows "the analysis takes 3-5 minutes; start?" + a cost estimate
 * 2. Running: polls /agents/runs/{trace_id}/progress and shows stage progress
 * 3. Done: top summary + Markdown reasoning + expandable 4 analyst reports + debate
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { buildAnalysisSections, type AnalysisSection } from '../analysis-sections'
import { useCompliance } from '@/hooks/use-compliance'
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from '@candlewise/base-ui/components/ui/dialog'
import { Tabs, TabsList, TabsTrigger, TabsContent } from '@candlewise/base-ui/components/ui/tabs'
import { Button } from '@candlewise/base-ui/components/ui/button'
import { useToast } from '@candlewise/base-ui/components/ui/toast'
import { HoverPopover } from '@candlewise/base-ui/components/ui/hover-popover'
import {
  subscribeSSE,
  tradingAgentsApi,
  type BudgetInfo,
  type DeepAnalysisResult,
  type ProgressResponse,
  type ProgressDataSource,
  type ProgressStage,
} from '@candlewise/api'
import {
  isTerminalProgressStatus,
  shouldContinueProgressWatch,
} from '../../../../src/lib/tradingagents-progress'

const STAGE_LABEL: Record<string, string> = {
  data_collection: 'Data preparation',
  market_analyst: 'Technical analyst',
  social_analyst: 'Sentiment analyst',
  news_analyst: 'News analyst',
  fundamentals_analyst: 'Fundamentals analyst',
  bull_bear_debate: 'Bull vs bear debate',
  research_manager: 'Research manager',
  trader: 'Trader decision',
  risk_judge: 'Risk verdict',
  final_decision: 'PM synthesis',
}

const DECISION_COLOR: Record<string, string> = {
  buy: 'text-emerald-600 dark:text-emerald-400',
  hold: 'text-amber-600 dark:text-amber-400',
  sell: 'text-rose-600 dark:text-rose-400',
}

const POLL_INTERVAL_MS = 2000

/** The trace_id of a stock's latest run, kept in localStorage; polling resumes when the dialog is reopened */
const STORAGE_KEY_PREFIX = 'candlewise:tradingagents:running:'
/** After how long a trace_id may no longer be running (avoids showing an expired trace as idle) */
const TRACE_MAX_AGE_MS = 60 * 60 * 1000  // matches the backend running lifecycle window, with room for recovery

function loadRunningTrace(stockSymbol: string): string | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_PREFIX + stockSymbol)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { traceId: string; startedAt: number }
    if (!parsed.traceId || !parsed.startedAt) return null
    if (Date.now() - parsed.startedAt > TRACE_MAX_AGE_MS) {
      localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
      return null
    }
    return parsed.traceId
  } catch {
    return null
  }
}

function saveRunningTrace(stockSymbol: string, traceId: string): void {
  try {
    localStorage.setItem(
      STORAGE_KEY_PREFIX + stockSymbol,
      JSON.stringify({ traceId, startedAt: Date.now() }),
    )
  } catch {
    /* ignore quota and similar errors */
  }
}

function clearRunningTrace(stockSymbol: string): void {
  try {
    localStorage.removeItem(STORAGE_KEY_PREFIX + stockSymbol)
  } catch {
    /* ignore */
  }
}

export interface DeepAnalysisModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  stockId: number
  stockName: string
  stockSymbol: string
  /** Past analysis (shown directly if there is one) */
  initialResult?: DeepAnalysisResult | null
}

export function DeepAnalysisModal({
  open,
  onOpenChange,
  stockId,
  stockName,
  stockSymbol,
  initialResult = null,
}: DeepAnalysisModalProps) {
  const { toast } = useToast()
  const [stage, setStage] = useState<'idle' | 'running' | 'done' | 'error'>('idle')
  const [traceId, setTraceId] = useState<string | null>(null)
  const [progress, setProgress] = useState<ProgressResponse | null>(null)
  const [result, setResult] = useState<DeepAnalysisResult | null>(initialResult)
  const [error, setError] = useState<string>('')
  const [budget, setBudget] = useState<BudgetInfo | null>(null)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  // SSE unsubscribe function (progress uses SSE first, falling back to polling on failure)
  const sseCloseRef = useRef<(() => void) | null>(null)

  /** Stop all progress listening (SSE + polling) */
  const stopWatching = useCallback(() => {
    if (timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
    if (sseCloseRef.current) {
      sseCloseRef.current()
      sseCloseRef.current = null
    }
  }, [])

  // Clean up progress listening when the dialog closes
  useEffect(() => {
    if (!open) stopWatching()
  }, [open, stopWatching])

  // Reset the initial state + ask the backend whether a run is in progress or done
  useEffect(() => {
    if (!open) return

    if (initialResult) {
      setResult(initialResult)
      setStage('done')
      return
    }

    // Reset to idle first (so the previous state doesn't linger), then query the backend asynchronously
    setStage('idle')
    setResult(null)
    setError('')
    setProgress(null)
    setTraceId(null)

    // Query 3 things concurrently:
    //   - findRunning: is there a run for this stock in the last 30 minutes
    //   - getLatestForStock: is there a result finished today (even after 30 minutes)
    //   - getBudget: this month's budget (shown in the idle state)
    // Priority: running > done (a result exists) > idle
    Promise.all([
      tradingAgentsApi.findRunning(stockSymbol).catch(() => ({ trace_id: null, status: 'none' as const })),
      tradingAgentsApi.getLatestForStock(stockSymbol).catch(() => null),
      tradingAgentsApi.getBudget().catch(() => null),
    ]).then(([runningInfo, latestResult, budgetInfo]) => {
      setBudget(budgetInfo)

      // Priority: running (really running) > done (today's cache; re-analysis allowed) > idle
      //   - stale / failed / success / none all count as "not running"
      //   - in any state, today's cache shows DoneView (with the "ignore cache and re-analyse" button)
      //   - in any state, IdleView's "Start analysis" button always works; the backend deduplicates

      // 1) Really running (the backend is the authority) -> running
      if (runningInfo.status === 'running' && runningInfo.trace_id) {
        const tid = runningInfo.trace_id
        setTraceId(tid)
        setStage('running')
        // The backend confirms it's running; even if the collection stage has no logs yet, SSE/polling take over
        tradingAgentsApi.getProgress(tid).then(resp => setProgress(resp))
        startWatching(tid)
        return
      }

      // 2) Backend stale/failed -> the old run died/failed; clear local traces and continue with the cache check
      //    Never back to running; the user may trigger again
      if (runningInfo.status === 'stale' || runningInfo.status === 'failed') {
        clearRunningTrace(stockSymbol)
      }

      // 3) localStorage fallback (just triggered and the backend hasn't logged yet); only when the backend says 'none'
      if (runningInfo.status === 'none') {
        const localTrace = loadRunningTrace(stockSymbol)
        if (localTrace) {
          setTraceId(localTrace)
          setStage('running')
          tradingAgentsApi.getProgress(localTrace).then(resp => setProgress(resp))
          startWatching(localTrace)
          return
        }
      }

      // 4) A result finished today -> done view (the user can click "ignore cache and re-analyse")
      if (latestResult) {
        latestResult.raw_data.from_cache = true
        setResult(latestResult)
        setStage('done')
        clearRunningTrace(stockSymbol)
        return
      }

      // 5) Nothing -> idle (the start button works; the backend guards idempotency)
      clearRunningTrace(stockSymbol)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialResult, stockSymbol])

  /** Handle one progress snapshot (the same state machine for SSE pushes and polling) */
  const handleProgressResponse = useCallback(
    async (resp: ProgressResponse) => {
      setProgress(resp)
      if (resp.status === 'success') {
        // Done; fetch the saved result
        stopWatching()
        clearRunningTrace(stockSymbol)
        const latest = await tradingAgentsApi.getLatestForStock(stockSymbol)
        if (latest) {
          setResult(latest)
          setStage('done')
        } else {
          setError('The result was not saved; check "AI history" later')
          setStage('error')
        }
      } else if (resp.status === 'failed') {
        stopWatching()
        clearRunningTrace(stockSymbol)
        setError(resp.run?.error || 'Analysis failed')
        setStage('error')
      } else if (resp.status === 'stale') {
        // The backend detected a zombie running state (past the whole run lifecycle window)
        // -> reset to idle automatically; the user can trigger again
        stopWatching()
        clearRunningTrace(stockSymbol)
        setTraceId('')
        setProgress(null)
        setStage('idle')
      } else if (resp.status === 'not_found') {
        // No snapshot from SSE/polling for now doesn't mean the run is gone; the backend running record may still be collecting.
        // Keep the trace so the next poll or a page refresh can take over.
        return
      }
    },
    [stockSymbol, stopWatching],
  )

  const pollProgress = useCallback(
    async (tid: string) => {
      try {
        const resp = await tradingAgentsApi.getProgress(tid)
        await handleProgressResponse(resp)
      } catch (e) {
        // A polling failure doesn't stop at once; count one error
        console.warn('progress poll error:', e)
      }
    },
    [handleProgressResponse],
  )

  /** Fallback: setInterval polling (when SSE isn't available) */
  const startPolling = useCallback(
    (tid: string) => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = setInterval(() => pollProgress(tid), POLL_INTERVAL_MS)
      void pollProgress(tid)
    },
    [pollProgress],
  )

  /** Start listening for progress: SSE first (server push); on failure/stream close, fall back to polling (kept as the fallback) */
  const startWatching = useCallback(
    (tid: string) => {
      stopWatching()
      let terminal = false
      sseCloseRef.current = subscribeSSE(`/agents/runs/${tid}/progress/stream`, {
        onEvent: (ev) => {
          if (ev.event === 'progress' && ev.data && typeof ev.data === 'object') {
            const resp = ev.data as ProgressResponse
            if (isTerminalProgressStatus(resp.status)) terminal = true
            void handleProgressResponse(resp)
          } else if (ev.event === 'done' && ev.data?.status && ev.data.status !== 'timeout') {
            terminal = !shouldContinueProgressWatch(ev.data.status, 'done')
            // The done event only carries the status, not the full run/result; fetch one more snapshot at the end
            // so the dialog doesn't stay on running if a proxy dropped the last progress event.
            if (terminal) void pollProgress(tid)
          }
        },
        onClosed: () => {
          // The server closed the stream normally: stop if final; otherwise (e.g. stream timeout) fall back to polling
          if (!terminal) startPolling(tid)
        },
        onFailed: () => {
          // SSE unavailable (old proxy buffering / network problem) -> fall back to polling
          startPolling(tid)
        },
      })
    },
    [handleProgressResponse, pollProgress, startPolling, stopWatching],
  )

  const handleStart = useCallback(async (force = false) => {
    setStage('running')
    setError('')
    setProgress(null)
    try {
      const triggerResp = await tradingAgentsApi.trigger(stockId, { force })
      const tid = triggerResp.trace_id || ''
      setTraceId(tid)
      if (!tid) {
        // The backend returned no trace_id; just show the message
        setStage('done')
        toast(triggerResp.message || 'Triggered', 'success')
        return
      }
      // Persist the trace_id so closing and reopening can resume progress
      saveRunningTrace(stockSymbol, tid)
      // Start listening for progress (SSE first, falling back to polling)
      startWatching(tid)
      // Fetch once right away to render the initial progress quickly
      pollProgress(tid)
    } catch (e) {
      setStage('error')
      setError(e instanceof Error ? e.message : 'Trigger failed')
    }
  }, [stockId, stockSymbol, startWatching, pollProgress, toast])

  const handleClose = useCallback(() => {
    stopWatching()
    onOpenChange(false)
  }, [onOpenChange, stopWatching])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[92vw] max-w-6xl max-h-[85vh] overflow-y-auto scrollbar">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            🧠 Deep research · {stockName} ({stockSymbol})
          </DialogTitle>
          <DialogDescription>
            TradingAgents multi-agent framework · for research and education only; not investment advice
          </DialogDescription>
        </DialogHeader>

        {stage === 'idle' && (
          <IdleView
            stockSymbol={stockSymbol}
            budget={budget}
            onStart={() => handleStart(false)}
            onCancel={handleClose}
          />
        )}

        {stage === 'running' && (
          <RunningView progress={progress} traceId={traceId || ''} onClose={handleClose} />
        )}

        {stage === 'done' && result && <DoneView
          result={result}
          stockSymbol={stockSymbol}
          onRerun={() => handleStart(true)}
        />}

        {stage === 'error' && (
          <div className="space-y-3 text-[13px]">
            <div className="rounded-lg bg-rose-500/10 border border-rose-500/30 p-3 text-rose-600">
              <div className="font-semibold mb-1">Analysis failed</div>
              <div className="text-[12px]">{error}</div>
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={handleClose}>Close</Button>
              <Button onClick={() => handleStart(false)}>Retry</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function IdleView({
  stockSymbol,
  budget,
  onStart,
  onCancel,
}: {
  stockSymbol: string
  budget: BudgetInfo | null
  onStart: () => void
  onCancel: () => void
}) {
  const overBudget = budget?.exceeded && budget.over_budget_action === 'reject'
  const est = budget?.estimate_next_run
  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-1.5">
        <div className="font-medium">About to analyse: {stockSymbol}</div>
        <div className="text-muted-foreground">
          Runs 4 kinds of analyst (technical / sentiment / news / fundamentals) + bull vs bear debate + risk + PM synthesis
        </div>
        <div className="text-[11px] text-muted-foreground mt-2 space-y-0.5">
          <div>⏱ Estimated time: 3-8 minutes</div>
          {est ? (
            <div>💰 Estimated cost: ${est.cost_low_usd.toFixed(2)} - ${est.cost_high_usd.toFixed(2)} ({est.model})</div>
          ) : (
            <div>💰 Estimated cost: loading...</div>
          )}
          <div>ℹ️ Runs in the background; you can close this dialog, and a notification is sent when it finishes</div>
        </div>
      </div>

      {/* This month's budget */}
      {budget && (
        <div className={`rounded-lg p-3 text-[12px] ${overBudget ? 'bg-rose-500/10 border border-rose-500/30' : 'bg-accent/20'}`}>
          <div className="flex items-center justify-between">
            <span className="font-medium">This month's budget</span>
            <span className={overBudget ? 'text-rose-600' : 'text-muted-foreground'}>
              ${budget.used.toFixed(2)} / ${budget.limit.toFixed(2)}
              {budget.runs_this_month > 0 && ` · ${budget.runs_this_month} runs`}
            </span>
          </div>
          {overBudget && (
            <div className="text-[11px] text-rose-600 mt-1">
              ⚠️ This month's budget is used up. To continue, raise `monthly_budget_usd` under "Agents → TradingAgents → Deep config".
            </div>
          )}
        </div>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>Cancel</Button>
        <Button onClick={onStart} disabled={overBudget}>Start analysis</Button>
      </div>
    </div>
  )
}

function RunningView({
  progress,
  traceId,
  onClose,
}: {
  progress: ProgressResponse | null
  traceId: string
  onClose: () => void
}) {
  const elapsed = progress?.elapsed_sec ?? 0
  const cost = progress?.total_cost_usd ?? 0
  const stages = progress?.stages ?? []

  return (
    <div className="space-y-4 text-[13px]">
      <div className="rounded-lg bg-accent/30 p-3 space-y-2">
        <div className="flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full bg-primary animate-pulse" />
          <span className="font-medium">Analysis in progress...</span>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {formatElapsed(elapsed)} elapsed · ${cost.toFixed(4)}
          </span>
        </div>
        {progress?.active_operation && (
          <div className="text-[11px] text-muted-foreground">
            {progress.active_operation.agent && (
              <>
                Current agent: <span className="font-mono">{progress.active_operation.agent}</span> ·{' '}
              </>
            )}
            Current operation: {progress.active_operation.kind === 'tool' ? 'data tool ' : ''}
            <span className="font-mono">{progress.active_operation.name}</span>
          </div>
        )}
        <div className="space-y-1 mt-3">
          {stages.length > 0 ? stages.map((s) => (
            <StageRow key={s.name} stage={s} />
          )) : (
            <div className="text-[12px] text-muted-foreground">Preparing...</div>
          )}
        </div>
        <div className="text-[10px] text-muted-foreground/70 mt-3 font-mono">
          trace_id: {traceId.slice(0, 16)}...
        </div>
      </div>

      <ToolkitDiagnostics
        summary={progress?.toolkit_summary}
        recent={progress?.toolkit_recent || []}
      />

      {progress?.data_sources && progress.data_sources.length > 0 && (
        <DataCollectionDiagnostics sources={progress.data_sources} />
      )}

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onClose}>
          Run in the background (notify when done)
        </Button>
      </div>
    </div>
  )
}

function DataCollectionDiagnostics({ sources }: { sources: ProgressDataSource[] }) {
  const labels: Record<string, string> = {
    quote: 'Quote',
    klines: 'K-lines',
    capital_flow: 'Money flow',
    events: 'Events',
    financial: 'Financials',
    technical: 'Technical indicators',
  }
  const statusLabels: Record<ProgressDataSource['status'], string> = {
    pending: 'Waiting',
    running: 'Requesting',
    done: 'Done',
    error: 'Failed (degraded)',
  }
  const statusClasses: Record<ProgressDataSource['status'], string> = {
    pending: 'text-muted-foreground',
    running: 'text-sky-600 dark:text-sky-400',
    done: 'text-emerald-600 dark:text-emerald-400',
    error: 'text-amber-600 dark:text-amber-400',
  }

  return (
    <div className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]">
      <div className="font-medium mb-1">Data preparation details</div>
      <div className="flex flex-wrap gap-x-4 gap-y-1">
        {sources.map((source) => (
          <span key={source.name} className={statusClasses[source.status]} title={source.error}>
            {labels[source.name] || source.name}: {statusLabels[source.status]}
          </span>
        ))}
      </div>
    </div>
  )
}

interface ToolkitDiagItem {
  action?: string
  method?: string
  symbol?: string
  chars?: number
  snippet?: string
  source?: string
  reason?: string
}
interface ToolkitDiagSummary {
  hit: number
  miss: number
  passthrough: number
  fallthrough?: number
  error: number
}

export function ToolkitDiagnostics({
  summary,
  recent,
  defaultOpen = false,
}: {
  summary: ToolkitDiagSummary | undefined
  recent: ToolkitDiagItem[]
  defaultOpen?: boolean
}) {
  if (!summary && recent.length === 0) return null

  const hit = summary?.hit ?? 0
  const miss = summary?.miss ?? 0
  const pass = summary?.passthrough ?? 0
  const fall = summary?.fallthrough ?? 0
  const err = summary?.error ?? 0
  const total = hit + miss + pass + fall + err

  const ACTION_CLS: Record<string, string> = {
    HIT: 'text-emerald-600 dark:text-emerald-400',
    MISS: 'text-amber-600 dark:text-amber-400',
    PASSTHROUGH: 'text-sky-600 dark:text-sky-400',
    FALLTHROUGH: 'text-orange-600 dark:text-orange-400',
    ERROR: 'text-rose-600',
  }

  return (
    <details className="rounded-lg border border-border/40 bg-accent/10 p-3 text-[12px]" open={defaultOpen}>
      <summary className="cursor-pointer flex items-center gap-2 flex-wrap">
        <span className="font-medium">Data injection diagnostics</span>
        <span className="text-[11px] text-muted-foreground">
          (Candlewise data → TradingAgents tools)
        </span>
        <span className="ml-auto text-[11px] whitespace-nowrap">
          <span className={ACTION_CLS.HIT}>HIT {hit}</span>
          <span className="text-muted-foreground"> · MISS {miss}</span>
          <span className={ACTION_CLS.PASSTHROUGH}> · passthrough {pass}</span>
          {fall > 0 && <span className={ACTION_CLS.FALLTHROUGH}> · fallback {fall}</span>}
          {err > 0 && <span className="text-rose-600"> · errors {err}</span>}
        </span>
      </summary>
      <div className="text-[10.5px] text-muted-foreground/80 mt-2 leading-relaxed">
        <span className={ACTION_CLS.HIT}>HIT</span>: Candlewise data used ·{' '}
        <span className={ACTION_CLS.MISS}>MISS</span>: matched but not implemented by Candlewise ·{' '}
        <span className={ACTION_CLS.PASSTHROUGH}>Passthrough</span>: not served by Candlewise; went to the upstream vendor ·{' '}
        <span className={ACTION_CLS.FALLTHROUGH}>Fallback</span>: Candlewise symbol but empty cache; went upstream
      </div>
      {total === 0 ? (
        <div className="text-[11px] text-muted-foreground mt-2">
          ⚠️ No tool calls recorded yet (TradingAgents may still be preparing).
        </div>
      ) : (
        <div className="mt-2 space-y-1 max-h-64 overflow-y-auto">
          {recent.map((h, i) => {
            const action = (h.action || '').toUpperCase()
            const row = (
              <div className="font-mono text-[10.5px] flex items-center gap-2 hover:bg-accent/30 px-1 rounded cursor-help w-full">
                <span className={`${ACTION_CLS[action] || 'text-muted-foreground'} w-20 shrink-0`}>
                  {action}
                </span>
                <span className="text-foreground/80 truncate flex-1 text-left">
                  {h.method} ({h.symbol || '-'})
                  {h.reason && <span className="text-muted-foreground"> · {h.reason}</span>}
                  {h.chars != null && <span className="text-muted-foreground"> · {h.chars} characters</span>}
                  {h.source && <span className="text-muted-foreground/70"> · {h.source}</span>}
                </span>
              </div>
            )
            const hasDetail = !!(h.snippet || h.reason)
            if (!hasDetail) return <div key={i}>{row}</div>
            return (
              <HoverPopover
                key={i}
                className="block w-full"
                trigger={row}
                title={
                  <span>
                    <span className={ACTION_CLS[action] || 'text-muted-foreground'}>{action}</span>
                    <span className="text-muted-foreground"> · {h.method}({h.symbol || '-'})</span>
                    {h.source && (
                      <span className="text-muted-foreground/70"> · {h.source}</span>
                    )}
                  </span>
                }
                content={
                  <div className="space-y-2">
                    {h.reason && (
                      <div className="text-[11px] text-amber-600 dark:text-amber-400">
                        {h.reason}
                      </div>
                    )}
                    {h.snippet && (
                      <pre className="whitespace-pre-wrap break-words font-mono text-[10.5px] leading-snug bg-accent/30 rounded p-2 text-foreground/85 max-h-[60vh] overflow-y-auto">
                        {h.snippet}
                        {h.chars != null && h.chars > h.snippet.length && (
                          <span className="text-muted-foreground/60">
                            {'\n\n'}...({h.chars} characters in total; showing the first {h.snippet.length})
                          </span>
                        )}
                      </pre>
                    )}
                  </div>
                }
                popoverClassName="w-[44rem] max-w-[90vw]"
                side="top"
                align="start"
              />
            )
          })}
        </div>
      )}
    </details>
  )
}

function StageRow({ stage }: { stage: ProgressStage }) {
  const label = STAGE_LABEL[stage.name] || stage.name
  const icon =
    stage.status === 'done' ? '✓' : stage.status === 'running' ? '🔄' : '⏸'
  const cls =
    stage.status === 'done'
      ? 'text-emerald-600 dark:text-emerald-400'
      : stage.status === 'running'
      ? 'text-primary'
      : 'text-muted-foreground/60'
  return (
    <div className={`flex items-center gap-2 text-[12px] ${cls}`}>
      <span className="w-4">{icon}</span>
      <span>{label}</span>
      {stage.cost_usd ? (
        <span className="ml-auto text-[10px] opacity-70 font-mono">
          ${stage.cost_usd.toFixed(4)}
        </span>
      ) : null}
    </div>
  )
}

function DoneView({
  result,
  stockSymbol,
  onRerun,
}: {
  result: DeepAnalysisResult
  stockSymbol: string
  onRerun: () => void
}) {
  const { isEnabled, shortDisclaimer } = useCompliance()
  const ratingEnabled = isEnabled('tradingagents_rating')
  const rawData = (result?.raw_data || {}) as Partial<DeepAnalysisResult['raw_data']>
  // No default rating: a missing decision is shown as research, never as "hold" (ADR-005).
  const sug = ratingEnabled ? rawData.suggestion : undefined
  const fromCache = rawData.from_cache
  const costUsd = rawData.cost_usd
  const sections = buildAnalysisSections(rawData, { includeDecision: ratingEnabled })
  const analysisDate = result.timestamp
    ? String(result.timestamp).slice(0, 10)
    : new Date().toISOString().slice(0, 10)

  return (
    <div className="space-y-4 text-[13px]">
      {fromCache && (
        <div className="rounded-lg bg-amber-500/10 border border-amber-500/30 p-2 text-[12px] text-amber-700 dark:text-amber-400 flex items-center justify-between">
          <span>ℹ️ Today's cache: this stock was already analysed today; showing the cached result (no new cost)</span>
          <Button variant="outline" size="sm" onClick={onRerun} className="ml-3 h-7 text-[11px]">
            Ignore cache and re-analyse
          </Button>
        </div>
      )}

      {/* Top summary (one line: decision + confidence + cost; the full reasoning is in the "Final decision" tab) */}
      <div className="rounded-lg bg-accent/30 px-4 py-2.5 flex items-center gap-3 flex-wrap">
        {sug ? (
          <>
            <span className={`text-[18px] font-bold ${DECISION_COLOR[sug.action] || ''}`}>
              {sug.action_label}
            </span>
            <span className="text-[12px] text-muted-foreground">
              Confidence {sug.confidence?.toFixed(1) ?? '-'} / 10
            </span>
          </>
        ) : (
          <span className="text-[14px] font-semibold" data-testid="deep-research-label">
            Research summary
          </span>
        )}
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-[11px] ml-auto"
          onClick={() => window.open(`/analysis/${stockSymbol}/${analysisDate}`, '_blank')}
        >
          View the detail page
        </Button>
        <span className="text-[10px] text-muted-foreground">
          Cost: ${costUsd?.toFixed(4) ?? '-'}
        </span>
      </div>

      {/* Shared tabs: final decision + four analysts + bull vs bear debate + risk debate (in full, with GFM tables) */}
      <AnalysisTabs sections={sections} />

      {/* Data injection diagnostics (past reports): from raw_data.toolkit_diagnostic */}
      {rawData.toolkit_diagnostic && (
        <ToolkitDiagnostics
          summary={rawData.toolkit_diagnostic.summary}
          recent={rawData.toolkit_diagnostic.recent || []}
        />
      )}

      {/* Disclaimer */}
      <div className="text-[10px] text-muted-foreground/70 italic border-t border-border/30 pt-2">
        {shortDisclaimer}
      </div>
    </div>
  )
}

/** Shared decision and analysis tabs. Content is assembled by buildAnalysisSections (shared by the dialog and the detail page); only tabs with content render. */
function AnalysisTabs({ sections }: { sections: AnalysisSection[] }) {
  if (sections.length === 0) return null
  return (
    <div className="rounded-lg border border-border/50 p-4">
      <Tabs defaultValue={sections[0].id}>
        <TabsList>
          {sections.map((s) => (
            <TabsTrigger key={s.id} value={s.id}>
              {s.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {sections.map((s) => (
          <TabsContent key={s.id} value={s.id}>
            <div className="prose prose-sm dark:prose-invert max-w-none leading-relaxed prose-headings:mt-4 prose-headings:mb-2 prose-p:my-2 prose-table:my-3 prose-th:px-3 prose-th:py-1.5 prose-td:px-3 prose-td:py-1.5 prose-table:text-[12px] prose-strong:text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.markdown}</ReactMarkdown>
            </div>
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec.toFixed(0)}s`
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}m${s.toString().padStart(2, '0')}s`
}
