import { AlertCircle, CheckCircle2, ChevronDown, FileClock, Gauge, ListTree, PauseCircle, Search, Wrench } from 'lucide-react'
import { useState } from 'react'
import type { AssistantTraceEvent } from '@candlewise/api'

interface TraceTimelineProps {
  events: AssistantTraceEvent[]
  live?: boolean
}

function describeExtensionEvent(event: AssistantTraceEvent): { label: string; icon: typeof FileClock } | null {
  if (event.data.extension !== 'tool_research') return null
  const data = event.data.data || {}
  switch (event.data.event) {
    case 'started': return { label: 'Researching available tools', icon: Search }
    case 'exposure': return {
      label: `Tool catalogue ready: ${data.direct_tools?.length || 0} direct, ${data.loaded_tools?.length || 0} loaded`,
      icon: Search,
    }
    case 'candidates_scored': return { label: `Screening tool candidates: ${data.candidates?.length || 0}`, icon: Search }
    case 'completed': return { label: `Tool research done: ${data.selected_tools?.length || 0} chosen`, icon: Search }
    case 'searched': return { label: `Tool search done: ${data.selected_tools?.length || 0} loaded`, icon: Search }
    case 'fallback': return { label: 'Tool research fell back; using the default tool set', icon: AlertCircle }
    default: return { label: `Extension event: ${event.data.event || 'unknown'}`, icon: FileClock }
  }
}

function describe(event: AssistantTraceEvent): { label: string; icon: typeof FileClock } {
  const name = typeof event.data.name === 'string' ? event.data.name : ''
  const extension = event.event === 'extension_event' ? describeExtensionEvent(event) : null
  if (extension) return extension
  switch (event.event) {
    case 'context_prepared': return { label: event.data.compressed ? 'Context compressed and prepared' : 'Context prepared', icon: FileClock }
    case 'step_updated': return { label: `Step ${event.data.step || ''}`, icon: ListTree }
    case 'tool_call_start': return { label: `Calling tool: ${name}`, icon: Wrench }
    case 'tool_result': return { label: event.data.ok ? `Tool done: ${name}` : `Tool failed: ${name}`, icon: event.data.ok ? CheckCircle2 : AlertCircle }
    case 'model_usage': return { label: `Model usage: input ${event.data.input_tokens || 0}, output ${event.data.output_tokens || 0}`, icon: Gauge }
    case 'approval_required': return { label: 'Waiting for your approval', icon: PauseCircle }
    case 'paused': return { label: 'Task paused', icon: PauseCircle }
    case 'done': return { label: 'Task complete', icon: CheckCircle2 }
    case 'error': return { label: 'Task failed', icon: AlertCircle }
    default: return { label: 'Task started', icon: FileClock }
  }
}

function detail(event: AssistantTraceEvent): string {
  if (event.event === 'tool_call_start' && event.data.arguments) {
    return JSON.stringify(event.data.arguments)
  }
  if (event.event === 'tool_result' && typeof event.data.preview === 'string') {
    return event.data.preview
  }
  return ''
}

function summary(events: AssistantTraceEvent[]): string {
  const toolCalls = events.filter((event) => event.event === 'tool_call_start').length
  const latest = [...events].reverse().find((event) => ['done', 'error', 'paused'].includes(event.event))
  const status = latest?.event === 'done'
    ? 'Done'
    : latest?.event === 'error'
    ? 'Failed'
    : latest?.event === 'paused'
    ? 'Waiting to continue'
    : 'Running'
  return toolCalls > 0 ? `${status} · ${toolCalls} tool calls` : status
}

export function TraceTimeline({ events, live = false }: TraceTimelineProps) {
  const [expanded, setExpanded] = useState(live)
  if (events.length === 0) return null
  return (
    <section data-testid="assistant-trace" className="rounded-lg border border-border/50 bg-background/70 px-3 py-2 text-[11px]">
      <button
        type="button"
        className="flex w-full items-center gap-1.5 text-left font-medium text-muted-foreground hover:text-foreground"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        <FileClock className="h-3.5 w-3.5 shrink-0" />
        <span>Run log</span>
        <span className="min-w-0 flex-1 truncate text-[10px] font-normal">{summary(events)}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 transition-transform ${expanded ? 'rotate-180' : ''}`} />
      </button>
      {expanded && (
        <ol className="mt-2 space-y-1.5 border-t border-border/40 pt-2">
          {events.map((event, index) => {
            const { label, icon: Icon } = describe(event)
            const eventDetail = detail(event)
            return (
              <li key={`${event.id ?? index}-${event.event}-${index}`} className="flex items-start gap-2 text-foreground">
                <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <div>{label}</div>
                  {eventDetail && <div className="break-words text-muted-foreground">{eventDetail}</div>}
                </div>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
