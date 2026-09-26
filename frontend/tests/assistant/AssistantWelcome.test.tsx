import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { AssistantWelcome } from '@/components/assistant/AssistantWelcome'

describe('AssistantWelcome', () => {
  it('starts a focused research question from a suggested entry point', async () => {
    const onSubmit = vi.fn()
    const user = userEvent.setup()

    render(<AssistantWelcome onSubmit={onSubmit} />)

    expect(screen.getByRole('heading', { name: 'What do you want to research today?' })).toBeTruthy()
    await user.click(screen.getByRole('button', { name: 'Check my holdings' }))

    expect(onSubmit).toHaveBeenCalledWith('Give my portfolio a full portfolio check: risks and key points to watch')
  })
})
