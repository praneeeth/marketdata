import { beforeEach, describe, expect, it, vi } from 'vitest'

const fetchAPI = vi.hoisted(() => vi.fn())
vi.mock('@panwatch/api/client', () => ({ fetchAPI }))

import { brokersApi } from '@panwatch/api/brokers'

describe('brokersApi.save', () => {
  beforeEach(() => fetchAPI.mockReset().mockResolvedValue({}))

  it('does not send priority or enabled unless asked (review #3)', async () => {
    await brokersApi.save('kite', { api_key: 'k' })
    const body = JSON.parse(fetchAPI.mock.calls[0][1].body)
    expect(body).toEqual({ credentials: { api_key: 'k' } })
  })

  it('sends them when given', async () => {
    await brokersApi.save('kite', {}, { priority: 2, enabled: false })
    const body = JSON.parse(fetchAPI.mock.calls[0][1].body)
    expect(body).toEqual({ credentials: {}, priority: 2, enabled: false })
  })
})
