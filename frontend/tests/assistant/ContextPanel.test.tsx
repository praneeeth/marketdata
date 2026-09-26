import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { ContextPanel } from '@/components/assistant/ContextPanel'

const detail = {
  conversation_id: 1,
  status: 'warning' as const,
  compression_available: true,
  usage: {
    total_tokens: 9000,
    budget_tokens: 12000,
    soft_limit_tokens: 8400,
    hard_limit_tokens: 10200,
    estimated: true,
    state: 'warning' as const,
    sections: [
      { name: 'system', tokens: 120 },
      { name: 'history', tokens: 7000 },
      { name: 'recent_messages', tokens: 1880 },
    ],
  },
  snapshot: {
    version: 2,
    mode: 'balanced' as const,
    summary: {
      goal: ['analyse holdings'],
      constraints: [],
      decisions: [],
      facts: [],
      current_state: 'waiting for the next step',
      open_items: ['add a risk note'],
      tool_findings: [],
    },
    source_message_count: 8,
    covered_until_message_id: 12,
    usage_before: {
      total_tokens: 10000,
      budget_tokens: 12000,
      soft_limit_tokens: 8400,
      hard_limit_tokens: 10200,
      estimated: true,
      state: 'warning' as const,
      sections: [],
    },
    usage_after: {
      total_tokens: 3000,
      budget_tokens: 12000,
      soft_limit_tokens: 8400,
      hard_limit_tokens: 10200,
      estimated: true,
      state: 'normal' as const,
      sections: [],
    },
    created_at: '2026-09-12T01:00:00Z',
  },
}

describe('ContextPanel', () => {
  it('shows the budget breakdown and invokes the selected compression mode', () => {
    const onCompress = vi.fn()
    render(<ContextPanel detail={detail} loading={false} compressing={false} onCompress={onCompress} />)

    expect(screen.getByText('Estimated input tokens: 9,000 / 12,000')).toBeTruthy()
    expect(screen.getByText('History')).toBeTruthy()
    expect(screen.getByText(/analyse holdings/)).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: 'Compress, keeping details' }))
    expect(onCompress).toHaveBeenCalledWith('preserve_details')
  })
})
