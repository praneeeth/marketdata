import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('@/components/BrokerConnections', () => ({ default: () => <div data-testid="broker-connections" /> }))

import DataSourcesPage from '@/pages/DataSources'

describe('DataSourcesPage (India-only)', () => {
  it('shows broker connections and labels global data as delayed/unofficial', () => {
    render(<DataSourcesPage />)
    expect(screen.getByTestId('broker-connections')).toBeTruthy()
    const global = screen.getByTestId('global-cues-source')
    expect(global.textContent).toContain('Delayed / unofficial')
    expect(global.textContent).toContain('GLOBAL_CUES_SOURCE=off')
    expect(document.body.textContent).not.toMatch(/Eastmoney|Tencent|Xueqiu|Sina Finance/)
  })
})
