import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MemoryRouter, useLocation } from 'react-router-dom'

import AssistantOpenBridge from '@/components/AssistantOpenBridge'

function LocationProbe() {
  const location = useLocation()
  return <output data-testid="location">{location.pathname}</output>
}

describe('AssistantOpenBridge', () => {
  it('translates the stock insight event into assistant route state', async () => {
    render(
      <MemoryRouter initialEntries={['/portfolio']}>
        <AssistantOpenBridge />
        <LocationProbe />
      </MemoryRouter>,
    )

    window.dispatchEvent(new CustomEvent('candlewise-open-chat', {
      detail: { symbol: 'INFY', market: 'IN', stockName: 'Infosys', pageContext: 'quote context' },
    }))

    await waitFor(() => expect(screen.getByTestId('location').textContent).toBe('/assistant'))
  })
})
