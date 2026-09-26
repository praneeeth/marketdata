import { useState } from 'react'
import { ShieldAlert } from 'lucide-react'
import type { AssistantApproval } from '@panwatch/api'

interface ApprovalCardProps {
  approval: AssistantApproval
  onDecision: (decision: 'approved' | 'rejected') => Promise<void> | void
}

const RISK_LABELS: Record<AssistantApproval['risk'], string> = {
  read: 'Read',
  write: 'Change',
  external: 'External action',
  destructive: 'Destructive action',
}

export function ApprovalCard({ approval, onDecision }: ApprovalCardProps) {
  const [decision, setDecision] = useState<'approved' | 'rejected' | null>(null)
  const [failed, setFailed] = useState(false)
  const disabled = approval.status !== 'pending' || decision !== null

  const decide = async (next: 'approved' | 'rejected') => {
    if (disabled) return
    setDecision(next)
    setFailed(false)
    try {
      await onDecision(next)
    } catch {
      setDecision(null)
      setFailed(true)
    }
  }

  const decisionStatus = approval.status === 'approved'
    ? 'Allowed and run'
    : approval.status === 'rejected'
      ? "Rejected; won't run"
      : ''
  const decisionStatusClass = approval.status === 'rejected'
    ? 'text-destructive'
    : 'text-emerald-600 dark:text-emerald-400'

  return (
    <section className="max-w-[85%] rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-3 text-[13px]">
      <div className="flex items-start gap-2.5">
        <span className="mt-0.5 rounded-md bg-amber-500/10 p-1.5 text-amber-600 dark:text-amber-400">
          <ShieldAlert className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h4 className="font-medium text-foreground">{approval.tool_title}</h4>
            <span className="rounded-full bg-amber-500/10 px-1.5 py-0.5 text-[10px] text-amber-700 dark:text-amber-300">
              {RISK_LABELS[approval.risk]}
            </span>
          </div>
          <p className="mt-1 text-muted-foreground">{approval.summary}</p>
          {decisionStatus ? (
            <p className={`mt-1.5 text-[11px] ${decisionStatusClass}`}>
              {decisionStatus}
            </p>
          ) : approval.expires_at && (
            <p className="mt-1.5 text-[11px] text-muted-foreground/80">
              Decide before {new Date(approval.expires_at).toLocaleString()}
            </p>
          )}
          {failed && <p className="mt-1.5 text-[11px] text-destructive">Failed to submit the decision; please try again.</p>}
          {approval.status === 'pending' && (
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                onClick={() => void decide('approved')}
                disabled={disabled}
                className="rounded-lg bg-primary px-2.5 py-1.5 text-[12px] font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {decision === 'approved' ? 'Submitting…' : 'Allow once'}
              </button>
              <button
                type="button"
                onClick={() => void decide('rejected')}
                disabled={disabled}
                className="rounded-lg border border-border bg-background px-2.5 py-1.5 text-[12px] font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
              >
                {decision === 'rejected' ? 'Submitting…' : 'Reject'}
              </button>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
