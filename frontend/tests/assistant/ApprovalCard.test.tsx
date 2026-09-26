import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { ApprovalCard } from '@/components/assistant/ApprovalCard'

describe('ApprovalCard', () => {
  it('allows exactly one explicit decision and shows the operation summary', async () => {
    const onDecision = vi.fn().mockResolvedValue(undefined)
    const user = userEvent.setup()
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: 'Create alert',
          risk: 'write',
          summary: 'Create a price alert for Infosys',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'pending',
        }}
        onDecision={onDecision}
      />,
    )

    expect(screen.getByText('Create a price alert for Infosys')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Allow once' }))

    expect(onDecision).toHaveBeenCalledTimes(1)
    expect(onDecision).toHaveBeenCalledWith('approved')
    expect((screen.getByRole('button', { name: 'Reject' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('keeps a decided card visible with its execution status', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-1',
          tool_title: 'Create alert',
          risk: 'write',
          summary: 'Create a price alert for Infosys',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'approved',
        }}
        onDecision={vi.fn()}
      />,
    )

    expect(screen.getByText('Allowed and run')).toBeTruthy()
    expect(screen.queryByRole('button', { name: 'Allow once' })).toBeNull()
  })

  it('shows a rejected card as a terminal decision without actions', () => {
    render(
      <ApprovalCard
        approval={{
          id: 'approval-2',
          tool_title: 'Create alert',
          risk: 'write',
          summary: 'Create a price alert for Infosys',
          expires_at: '2026-09-11T00:10:00Z',
          status: 'rejected',
        }}
        onDecision={vi.fn()}
      />,
    )

    const status = screen.getByText("Rejected; won't run")
    expect(status.className).toContain('text-destructive')
    expect(screen.queryByRole('button', { name: 'Reject' })).toBeNull()
  })
})
