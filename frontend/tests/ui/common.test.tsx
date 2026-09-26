import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { FALLBACK_SHORT_DISCLAIMER } from '@candlewise/api/compliance'
import { Change } from '@/components/common/Change'
import { EmptyState, ErrorState, LoadingState, errorMessage } from '@/components/common/states'
import { DisclaimerNote, PageHeader, SimulationBadge } from '@/components/common/Brand'

describe('Change', () => {
  it('pairs colour with an arrow, a sign and spoken direction', () => {
    const { container } = render(<Change value={1.5} />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.direction).toBe('up')
    expect(el.className).toContain('text-up')
    expect(el.textContent).toBe('▲up +1.50%')
  })

  it('shows falls in red with a down arrow and a minus sign', () => {
    const { container } = render(<Change value={-250000} kind="inr" />)
    const el = container.firstElementChild as HTMLElement
    expect(el.className).toContain('text-down')
    expect(el.textContent).toBe('▼down -₹2.5 L')
  })

  it('is neutral and arrowless when unchanged', () => {
    const { container } = render(<Change value={0} kind="number" />)
    const el = container.firstElementChild as HTMLElement
    expect(el.dataset.direction).toBe('flat')
    expect(el.textContent).toBe('unchanged 0.00')
  })
})

describe('states', () => {
  it('announces loading', () => {
    render(<LoadingState label="Loading holdings…" />)
    expect(screen.getByRole('status').textContent).toContain('Loading holdings…')
  })

  it('draws skeleton rows', () => {
    render(<LoadingState rows={3} label="Loading list" />)
    expect(screen.getByRole('status', { name: 'Loading list' })).toBeTruthy()
  })

  it('shows an empty state with an action', () => {
    render(<EmptyState title="No stocks yet" description="Add one to start" action={<button>Add stock</button>} />)
    expect(screen.getByText('No stocks yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: 'Add stock' })).toBeTruthy()
  })

  it('shows an error with a retry', async () => {
    const retry = vi.fn()
    render(<ErrorState message="Network down" onRetry={retry} />)
    expect(screen.getByRole('alert').textContent).toContain('Network down')
    await userEvent.click(screen.getByRole('button', { name: /try again/i }))
    expect(retry).toHaveBeenCalledOnce()
  })

  it('reads error messages', () => {
    expect(errorMessage(new Error('boom'))).toBe('boom')
    expect(errorMessage('plain')).toBe('plain')
    expect(errorMessage(42, 'fallback')).toBe('fallback')
  })
})

describe('brand pieces', () => {
  it('always renders the disclaimer text', () => {
    render(<DisclaimerNote />)
    expect(screen.getByTestId('research-disclaimer').textContent).toContain(FALLBACK_SHORT_DISCLAIMER)
  })

  it('labels simulation clearly', () => {
    render(<SimulationBadge />)
    expect(screen.getByText('Simulation')).toBeTruthy()
  })

  it('renders a page header', () => {
    render(<PageHeader title="Holdings" eyebrow="Portfolio" description="Your accounts" actions={<button>Add</button>} />)
    expect(screen.getByRole('heading', { level: 1, name: 'Holdings' })).toBeTruthy()
    expect(screen.getByText('Portfolio')).toBeTruthy()
  })
})
