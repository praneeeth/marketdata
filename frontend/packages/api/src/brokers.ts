import { fetchAPI } from './client'

/** Broker connections (India fork, Phase 2). Secrets are never returned: only `key_hint`. */
export type BrokerStatus = 'connected' | 'disconnected' | 'expired' | 'error'
export type BrokerLogin = 'redirect' | 'totp' | 'none'
export type DataQuality = 'official_realtime' | 'official_delayed' | 'unofficial_delayed'

export interface BrokerProviderInfo {
  provider: string
  label: string
  fields: string[]
  login: BrokerLogin
  notes: string
  quality: DataQuality | null
}

export interface BrokerConnection {
  provider: string
  label: string
  status: BrokerStatus
  key_hint: string
  priority: number
  enabled: boolean
  session_expires_at: string | null
  last_error: string
  quality: DataQuality | null
}

export interface BrokerOverview {
  vault_configured: boolean
  available: BrokerProviderInfo[]
  connections: BrokerConnection[]
}

export const brokersApi = {
  list: () => fetchAPI<BrokerOverview>('/brokers'),
  /** Omitted `priority`/`enabled` keep the stored values, so editing keys never resets them. */
  save: (
    provider: string,
    credentials: Record<string, string>,
    options: { priority?: number; enabled?: boolean } = {},
  ) =>
    fetchAPI<BrokerConnection>(`/brokers/${provider}`, {
      method: 'PUT',
      body: JSON.stringify({ credentials, ...options }),
    }),
  remove: (provider: string) =>
    fetchAPI<{ deleted: boolean }>(`/brokers/${provider}`, { method: 'DELETE' }),
  disconnect: (provider: string) =>
    fetchAPI<BrokerConnection>(`/brokers/${provider}/disconnect`, { method: 'POST' }),
  startLogin: (provider: string) =>
    fetchAPI<{ login_url: string }>(`/brokers/${provider}/login`, { method: 'POST' }),
  loginWithTotp: (provider: string, totp: string) =>
    fetchAPI<BrokerConnection>(`/brokers/${provider}/totp`, {
      method: 'POST',
      body: JSON.stringify({ totp }),
    }),
}

export const DATA_QUALITY_LABEL: Record<DataQuality, string> = {
  official_realtime: 'Official, real-time',
  official_delayed: 'Official, delayed',
  unofficial_delayed: 'Delayed / unofficial',
}
