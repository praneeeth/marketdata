import { fetchAPI } from './client'

/** Personal access token (PAT): a credential just for the MCP endpoint */
export interface PatItem {
  id: number
  name: string
  prefix: string
  scopes: string[]
  expires_at: string | null
  last_used_at: string | null
  revoked_at: string | null
  created_at: string | null
  revoked: boolean
}

/** Create response: carries the one-time plaintext token as well */
export interface PatCreated extends PatItem {
  token: string
}

export interface CreatePatBody {
  name?: string
  scopes?: string[]
  /** Days until expiry; null = never expires */
  expires_in_days?: number | null
}

export const patsApi = {
  list: () => fetchAPI<{ items: PatItem[] }>('/pats'),

  create: (body: CreatePatBody) =>
    fetchAPI<PatCreated>('/pats', {
      method: 'POST',
      body: JSON.stringify(body),
    }),

  revoke: (id: number) =>
    fetchAPI<{ ok: boolean; id: number }>(`/pats/${encodeURIComponent(String(id))}`, {
      method: 'DELETE',
    }),
}
