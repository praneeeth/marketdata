import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const api = vi.hoisted(() => ({
  list: vi.fn(),
  save: vi.fn(),
  remove: vi.fn(),
  disconnect: vi.fn(),
  startLogin: vi.fn(),
  loginWithTotp: vi.fn(),
}))

vi.mock('@panwatch/api/brokers', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@panwatch/api/brokers')>()
  return { ...actual, brokersApi: api }
})

import BrokerConnections, { readLoginResult } from '@/components/BrokerConnections'

const AVAILABLE = [
  {
    provider: 'kite',
    label: 'Zerodha Kite Connect',
    fields: ['api_key', 'api_secret'],
    login: 'redirect',
    notes: 'Kite notes',
    quality: 'official_realtime',
  },
  {
    provider: 'angel',
    label: 'Angel One SmartAPI',
    fields: ['api_key', 'client_code', 'pin'],
    login: 'totp',
    notes: 'Angel notes',
    quality: 'official_realtime',
  },
  {
    provider: 'yfinance',
    label: 'Yahoo Finance (development only, unofficial and delayed)',
    fields: [],
    login: 'none',
    notes: 'dev only',
    quality: 'unofficial_delayed',
  },
]

function overview(connections: unknown[] = [], vault = true) {
  return { vault_configured: vault, available: AVAILABLE, connections }
}

const realLocation = window.location

describe('BrokerConnections', () => {
  afterEach(() => {
    Object.defineProperty(window, 'location', { value: realLocation, writable: true })
  })

  beforeEach(() => {
    vi.clearAllMocks()
    window.history.replaceState(null, '', '/datasources')
  })

  it('warns when the credential vault is not configured', async () => {
    api.list.mockResolvedValue(overview([], false))
    render(<BrokerConnections />)
    expect((await screen.findByRole('alert')).textContent).toContain('CREDENTIALS_MASTER_KEY')
    expect(screen.queryByTestId('broker-kite')).toBeNull()
  })

  it('saves keys, masking secret fields', async () => {
    api.list.mockResolvedValue(overview())
    api.save.mockResolvedValue({})
    render(<BrokerConnections />)
    const secret = await screen.findByLabelText('API secret', { selector: '#kite-api_secret' })
    expect(secret.getAttribute('type')).toBe('password')
    fireEvent.change(screen.getByLabelText('API key', { selector: '#kite-api_key' }), {
      target: { value: 'k1' },
    })
    fireEvent.change(secret, { target: { value: 's1' } })
    fireEvent.click(screen.getAllByRole('button', { name: /Save/ })[0])
    await waitFor(() => expect(api.save).toHaveBeenCalledWith('kite', { api_key: 'k1', api_secret: 's1' }))
    expect(api.list).toHaveBeenCalledTimes(2)
  })

  it('shows expiry status and starts a redirect login', async () => {
    api.list.mockResolvedValue(
      overview([
        {
          provider: 'kite',
          label: 'Zerodha Kite Connect',
          status: 'expired',
          key_hint: 'kit••••••_123',
          priority: 0,
          enabled: true,
          session_expires_at: null,
          last_error: '',
          quality: 'official_realtime',
        },
      ]),
    )
    api.startLogin.mockResolvedValue({ login_url: 'https://kite.example/login' })
    const assign = vi.fn()
    Object.defineProperty(window, 'location', {
      value: { ...window.location, assign, search: '' },
      writable: true,
    })
    render(<BrokerConnections />)
    expect((await screen.findByTestId('broker-status-kite')).textContent).toContain('Session expired, log in again')
    expect(screen.getByText(/kit••••••_123/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /Log in to Zerodha/ }))
    await waitFor(() => expect(assign).toHaveBeenCalledWith('https://kite.example/login'))
  })

  it('logs in to Angel with a 6-digit TOTP only', async () => {
    api.list.mockResolvedValue(
      overview([
        {
          provider: 'angel',
          label: 'Angel One SmartAPI',
          status: 'disconnected',
          key_hint: 'C1••••••',
          priority: 0,
          enabled: true,
          session_expires_at: null,
          last_error: '',
          quality: 'official_realtime',
        },
      ]),
    )
    api.loginWithTotp.mockResolvedValue({})
    render(<BrokerConnections />)
    const input = await screen.findByLabelText('Authenticator code')
    const button = screen.getByRole('button', { name: /^Log in$/ })
    fireEvent.change(input, { target: { value: '12a34' } })
    expect((input as HTMLInputElement).value).toBe('1234')
    expect((button as HTMLButtonElement).disabled).toBe(true)
    fireEvent.change(input, { target: { value: '123456' } })
    fireEvent.click(button)
    await waitFor(() => expect(api.loginWithTotp).toHaveBeenCalledWith('angel', '123456'))
  })

  it('labels unofficial data and shows API errors', async () => {
    api.list.mockResolvedValue(overview())
    api.save.mockRejectedValue(new Error('Unofficial data is disabled on this server.'))
    render(<BrokerConnections />)
    const card = await screen.findByTestId('broker-yfinance')
    expect(card.textContent).toContain('Delayed / unofficial')
    fireEvent.click(screen.getByRole('button', { name: 'Enable' }))
    expect(await screen.findByText('Unofficial data is disabled on this server.')).toBeTruthy()
  })

  it('shows connected sessions with expiry and allows logging out', async () => {
    api.list.mockResolvedValue(
      overview([
        {
          provider: 'kite',
          label: 'Zerodha Kite Connect',
          status: 'connected',
          key_hint: 'kit••••••_123',
          priority: 0,
          enabled: true,
          session_expires_at: '2026-09-24T06:00:00+05:30',
          last_error: '',
          quality: 'official_realtime',
        },
      ]),
    )
    api.disconnect.mockResolvedValue({})
    render(<BrokerConnections />)
    expect((await screen.findByText(/Session valid until/)).textContent).toContain('IST')
    fireEvent.click(screen.getByRole('button', { name: /Log out/ }))
    await waitFor(() => expect(api.disconnect).toHaveBeenCalledWith('kite'))
  })

  it('reports list failures', async () => {
    api.list.mockRejectedValue(new Error('network down'))
    render(<BrokerConnections />)
    expect(await screen.findByText('network down')).toBeTruthy()
  })
})

describe('readLoginResult', () => {
  it('parses the redirect result', () => {
    expect(readLoginResult('?broker=kite&status=connected')).toEqual({ provider: 'kite', status: 'connected' })
    expect(readLoginResult('?broker=kite')).toBeNull()
    expect(readLoginResult('')).toBeNull()
  })
})
