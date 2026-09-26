import { describe, expect, it, vi } from 'vitest'

import {
  loadPortfolioPageBackgroundData,
  loadPortfolioPageCoreData,
  loadPortfolioPageQuoteData,
  buildPortfolioStockKeys,
} from '@/lib/portfolio-page-data'

describe('portfolio page loading', () => {
  it('resolves core data without waiting for background lanes', async () => {
    const signal = new AbortController().signal
    const api = {
      loadStocks: vi.fn().mockResolvedValue([{ symbol: 'INFY', market: 'IN' }]),
      loadPortfolio: vi.fn().mockResolvedValue({ accounts: [{ positions: [] }] }),
      loadMarketStatus: vi.fn(),
      buildQuoteItems: vi.fn(),
      loadQuotes: vi.fn(),
      loadSuggestions: vi.fn(),
      loadPriceAlerts: vi.fn(),
      loadKlines: vi.fn(),
    }

    const result = await loadPortfolioPageCoreData(api, signal)

    expect(result).toEqual({
      stocks: [{ symbol: 'INFY', market: 'IN' }],
      portfolio: { accounts: [{ positions: [] }] },
    })
    expect(api.loadMarketStatus).not.toHaveBeenCalled()
    expect(api.loadQuotes).not.toHaveBeenCalled()
    expect(api.loadKlines).not.toHaveBeenCalled()
  })

  it('runs background lanes after core data and passes the same signal', async () => {
    const signal = new AbortController().signal
    const items = [{ symbol: 'INFY', market: 'IN' }]
    const api = {
      loadMarketStatus: vi.fn().mockResolvedValue([{ code: 'IN' }]),
      buildQuoteItems: vi.fn().mockReturnValue(items),
      loadSuggestions: vi.fn().mockResolvedValue({}),
      loadPriceAlerts: vi.fn().mockResolvedValue({}),
      loadKlines: vi.fn().mockResolvedValue({ 'IN:INFY': { trend: 'bullish alignment' } }),
    }
    const stocks = [{ symbol: 'INFY', market: 'IN' }]
    const portfolio = { accounts: [{ positions: [] }] }

    const result = await loadPortfolioPageBackgroundData(api, stocks, portfolio, signal)

    expect(api.buildQuoteItems).toHaveBeenCalledWith(stocks, portfolio)
    expect(api.loadMarketStatus).toHaveBeenCalledWith(signal)
    expect(api.loadSuggestions).toHaveBeenCalledWith(items, signal)
    expect(api.loadPriceAlerts).toHaveBeenCalledWith(items, signal)
    expect(api.loadKlines).toHaveBeenCalledWith(items, signal)
    expect(result).toEqual({
      marketStatus: [{ code: 'IN' }],
      suggestions: {},
      priceAlerts: {},
      klines: { 'IN:INFY': { trend: 'bullish alignment' } },
    })
  })

  it('loads quotes as the priority lane before the page is revealed', async () => {
    const signal = new AbortController().signal
    const items = [{ symbol: 'INFY', market: 'IN' }]
    const api = {
      buildQuoteItems: vi.fn().mockReturnValue(items),
      loadQuotes: vi.fn().mockResolvedValue([{ symbol: 'INFY', market: 'IN' }]),
    }

    const result = await loadPortfolioPageQuoteData(
      api,
      [{ symbol: 'INFY', market: 'IN' }],
      { accounts: [] },
      signal,
    )

    expect(api.buildQuoteItems).toHaveBeenCalledWith(
      [{ symbol: 'INFY', market: 'IN' }],
      { accounts: [] },
    )
    expect(api.loadQuotes).toHaveBeenCalledWith(items, signal)
    expect(result).toEqual({ quotes: [{ symbol: 'INFY', market: 'IN' }] })
  })

  it('formats suggestion stock keys as market then symbol', () => {
    expect(buildPortfolioStockKeys([
      { symbol: 'INFY', market: 'IN' },
      { symbol: 'TCS', market: 'IN' },
    ])).toBe('IN:INFY,IN:TCS')
  })
})
