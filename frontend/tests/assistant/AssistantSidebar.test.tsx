import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantSidebar } from '@/components/assistant/AssistantSidebar'

describe('AssistantSidebar', () => {
  it('opens a prior research conversation and exposes a new-research action', async () => {
    const onOpen = vi.fn()
    const onCreate = vi.fn()
    const user = userEvent.setup()

    render(
      <AssistantSidebar
        conversations={[
          { id: 8, title: 'Infosys trend', stock_symbol: 'INFY', stock_market: 'IN', created_at: '2026-09-12T00:00:00Z' },
        ]}
        activeConversationId={null}
        onOpen={onOpen}
        onCreate={onCreate}
        onDelete={vi.fn()}
      />,
    )

    await user.click(screen.getByRole('button', { name: 'New research' }))
    await user.click(screen.getByRole('button', { name: 'Infosys trend' }))

    expect(onCreate).toHaveBeenCalledTimes(1)
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ id: 8 }))
  })
})
