import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  status: vi.fn(),
  getAck: vi.fn(),
  acknowledge: vi.fn(),
}))

vi.mock('@candlewise/api/compliance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@candlewise/api/compliance')>()
  return { ...actual, complianceApi: { ...actual.complianceApi, ...api } }
})

import DisclaimerConsentDialog from '@/components/DisclaimerConsentDialog'
import { ComplianceProvider, useCompliance } from '@/hooks/use-compliance'

// Stands in for the Dashboard welcome guide, which opens only once this flag is true.
function AckProbe() {
  const { disclaimerAcknowledged } = useCompliance()
  return <output data-testid="ack">{String(disclaimerAcknowledged)}</output>
}

function renderApp() {
  return render(
    <ComplianceProvider>
      <DisclaimerConsentDialog />
      <AckProbe />
    </ComplianceProvider>,
  )
}

describe('disclaimer consent gates the welcome guide', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    api.status.mockResolvedValue({ disclaimer: { version: 'v1', short: 's', long: 'Long notice' }, features: {} })
    api.acknowledge.mockResolvedValue(undefined)
  })

  it('keeps the guide closed until a first-time user accepts the disclaimer', async () => {
    api.getAck.mockResolvedValue({ acknowledged_version: '', current_version: 'v1', required: true })
    renderApp()

    await screen.findByTestId('disclaimer-consent')
    expect(screen.getByTestId('ack').textContent).toBe('false')

    await screen.findByText('Long notice')
    fireEvent.click(screen.getByLabelText('I understand this is not investment advice'))
    fireEvent.click(screen.getByRole('button', { name: 'I understand' }))

    await waitFor(() => expect(screen.getByTestId('ack').textContent).toBe('true'))
    expect(api.acknowledge).toHaveBeenCalledWith('v1')
    await waitFor(() => expect(screen.queryByTestId('disclaimer-consent')).toBeNull())
  })

  it('lets returning users straight through', async () => {
    api.getAck.mockResolvedValue({ acknowledged_version: 'v1', current_version: 'v1', required: false })
    renderApp()

    await waitFor(() => expect(screen.getByTestId('ack').textContent).toBe('true'))
    expect(screen.queryByTestId('disclaimer-consent')).toBeNull()
  })

  it('fails closed when the acknowledgement check errors', async () => {
    api.getAck.mockRejectedValue(new Error('network'))
    renderApp()

    await screen.findByTestId('disclaimer-consent')
    expect(screen.getByTestId('ack').textContent).toBe('false')
  })
})
