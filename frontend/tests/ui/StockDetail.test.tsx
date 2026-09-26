import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  quote: vi.fn(),
  klineSummary: vi.fn(),
  news: vi.fn(),
  latest: vi.fn(),
}))

vi.mock('@candlewise/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@candlewise/api')>()
  return {
    ...actual,
    stocksApi: { ...actual.stocksApi, list: api.list },
    insightApi: { ...actual.insightApi, quote: api.quote, klineSummary: api.klineSummary, news: api.news },
    tradingAgentsApi: { ...actual.tradingAgentsApi, getLatestForStock: api.latest },
  }
})
vi.mock('@candlewise/biz-ui/components/InteractiveKline', () => ({ default: () => <div data-testid="chart" /> }))
vi.mock('@candlewise/biz-ui/components/stock-insight-modal', () => ({ default: () => null }))
vi.mock('@candlewise/biz-ui/components/deep-analysis-modal', () => ({ DeepAnalysisModal: () => null }))

import StockDetailPage from '@/pages/StockDetail'

const SUMMARY = `### Summary
Revenue grew steadily.
### Bull case
- Strong deal wins
### Bear case
- Weak discretionary spend
### Key risks
- Currency moves
### Upcoming events
- Results on 16 Oct
### Sources
- Market report, 25 Sep`

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/stock/INFY']}>
      <Routes>
        <Route path="/stock/:symbol" element={<StockDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  api.list.mockResolvedValue([{ id: 7, symbol: 'INFY', name: 'Infosys', market: 'IN', sort_order: 1, agents: [] }])
  api.quote.mockResolvedValue({
    symbol: 'INFY', current_price: 1000.2, change_pct: -1.41, change_amount: -14.3, prev_close: 1014.5,
    open_price: 999.5, high_price: 1004.2, low_price: 991.6, volume: 10240700, source: 'kite', quality: 'realtime',
  })
  api.klineSummary.mockResolvedValue({
    summary: { asof: '2026-09-25', last_close: 1000.2, trend: 'bearish alignment', support_s: 991.6, resistance_s: 1044.5, change_5d: -4.87, change_20d: -12.57 },
  })
  api.news.mockResolvedValue([
    { source: 'nse', source_label: 'NSE filing', title: 'Board meeting intimation', publish_time: '2026-09-26T04:30:00Z', url: 'https://example.com/a' },
  ])
  api.latest.mockResolvedValue({
    agent_name: 'tradingagents', title: 't', content: 'c', timestamp: '2026-09-26',
    raw_data: { research_summary: SUMMARY },
  })
})

describe('StockDetailPage', () => {
  it('shows price with rupee, sign and arrow, then the structured research summary', async () => {
    renderPage()
    expect(await screen.findByRole('heading', { level: 1, name: 'Infosys' })).toBeTruthy()
    await waitFor(() => expect(screen.getByText('₹1,000.20')).toBeTruthy())
    expect(screen.getAllByText((_, el) => el?.getAttribute('data-direction') === 'down').length).toBeGreaterThan(0)

    const research = await screen.findByRole('article')
    await waitFor(() => expect(within(research).getByText('Strong deal wins')).toBeTruthy())
    expect(within(research).getByText('Weak discretionary spend')).toBeTruthy()
    expect(within(research).getByText('Currency moves')).toBeTruthy()
    expect(within(research).getByText('Results on 16 Oct')).toBeTruthy()
    expect(within(research).getByText('₹991.60', { exact: false })).toBeTruthy()
    expect(within(research).getByText('Board meeting intimation')).toBeTruthy()
    expect(within(research).getByTestId('research-disclaimer')).toBeTruthy()
  })

  it('shows an empty research state with the disclaimer still visible', async () => {
    api.latest.mockResolvedValue(null)
    renderPage()
    expect(await screen.findByText('No research summary yet')).toBeTruthy()
    expect(screen.getByRole('button', { name: /run deep research/i })).toBeTruthy()
    expect(screen.getByTestId('research-disclaimer')).toBeTruthy()
  })

  it('shows an error state with retry when the price fails', async () => {
    api.quote.mockRejectedValue(new Error('Broker session expired'))
    renderPage()
    expect(await screen.findByText("Couldn't load the price")).toBeTruthy()
    expect(screen.getByText('Broker session expired')).toBeTruthy()
  })

  it('shows empty news', async () => {
    api.news.mockResolvedValue([])
    renderPage()
    expect(await screen.findByText('No recent news')).toBeTruthy()
  })
})
