import { fetchAPI } from './client'

/** Global market cues (context only; not investable research). Served by GET /api/market/global. */
export interface GlobalCue {
  key: string
  name: string
  group: 'US' | 'Asia' | 'Europe' | 'Commodities' | 'Currency'
  last: number | null
  change: number | null
  change_pct: number | null
  as_of: string | null
}

export type GlobalMarkets =
  | { enabled: false; cues: [] }
  | {
      enabled: true
      source: string
      quality: 'official_realtime' | 'official_delayed' | 'unofficial_delayed'
      quality_label: string
      cues: GlobalCue[]
    }

export const globalMarketsApi = {
  get: () => fetchAPI<GlobalMarkets>('/market/global'),
}
