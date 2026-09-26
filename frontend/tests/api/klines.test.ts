import { afterEach, describe, expect, it, vi } from 'vitest'

import { klinesApi } from '@panwatch/api/klines'

describe('klinesApi', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('loads multiple summaries with one batch request', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify({
        code: 0,
        success: true,
        data: [{ symbol: 'INFY', market: 'IN', summary: { trend: 'bullish alignment' } }],
        message: '',
      }), { status: 200, headers: { 'Content-Type': 'application/json' } }),
    )

    await klinesApi.summaryBatch([
      { symbol: 'INFY', market: 'IN' },
      { symbol: '00700', market: 'HK' },
    ])

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0]?.[0]).toBe('/api/klines/summary/batch')
    expect(JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body))).toEqual({
      items: [
        { symbol: 'INFY', market: 'IN' },
        { symbol: '00700', market: 'HK' },
      ],
    })
  })
})
