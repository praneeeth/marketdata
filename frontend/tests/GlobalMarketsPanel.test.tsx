import { render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({ get: vi.fn() }))
vi.mock('@candlewise/api/global-markets', () => ({ globalMarketsApi: api }))

import GlobalMarketsPanel, { formatLevel } from '@/components/GlobalMarketsPanel'

const cue = (key: string, name: string, group: string, last: number | null, change_pct: number | null) => ({
  key, name, group, last, change: null, change_pct, as_of: null,
})

describe('GlobalMarketsPanel', () => {
  // Braces matter: a function returned from beforeEach is run as a cleanup hook.
  beforeEach(() => {
    api.get.mockReset()
  })

  it('shows cues grouped with the data-quality label', async () => {
    api.get.mockResolvedValue({
      enabled: true,
      source: 'yahoo',
      quality: 'unofficial_delayed',
      quality_label: 'Delayed / unofficial',
      cues: [
        cue('USDINR', 'USD/INR', 'Currency', 83.21, -0.17),
        cue('SPX', 'S&P 500', 'US', 6612.5, 0.49),
        cue('HSI', 'Hang Seng', 'Asia', null, null),
      ],
    })
    render(<GlobalMarketsPanel />)
    const panel = await screen.findByTestId('global-markets')
    expect(panel.textContent).toContain('Delayed / unofficial')
    const rows = panel.querySelectorAll('[data-testid^="cue-"]')
    expect(Array.from(rows).map((r) => r.getAttribute('data-testid'))).toEqual(['cue-SPX', 'cue-HSI', 'cue-USDINR'])
    expect(screen.getByTestId('cue-SPX').textContent).toContain('+0.49%')
    expect(screen.getByTestId('cue-HSI').textContent).toContain('--')
    expect(screen.getByTestId('cue-USDINR').textContent).toContain('83.21')
  })

  it('renders nothing when switched off', async () => {
    api.get.mockResolvedValue({ enabled: false, cues: [] })
    const { container } = render(<GlobalMarketsPanel />)
    await Promise.resolve()
    expect(container.textContent).toBe('')
  })

  it('says so when the request fails', async () => {
    api.get.mockRejectedValue(new Error('down'))
    render(<GlobalMarketsPanel />)
    expect((await screen.findByTestId('global-markets')).textContent).toContain('unavailable')
  })
})

describe('formatLevel', () => {
  it('formats levels for Indian readers', () => {
    expect(formatLevel(null, 'SPX')).toBe('--')
    expect(formatLevel(38500.4, 'N225')).toBe('38,500')
    expect(formatLevel(83.214, 'USDINR')).toBe('83.21')
    expect(formatLevel(71.5, 'BRENT')).toBe('71.50')
  })
})
