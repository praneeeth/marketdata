import { fetchAPI } from './client'

/** Research-only compliance status served by GET /api/compliance/status (ADR-002). */
export interface ComplianceStatus {
  mode: 'research_only' | 'ra_registered'
  disclaimer: { version: string; short: string; long: string }
  simulation: { label: string; notice: string }
  research_analyst: null | {
    registration_number: string
    name: string
    contact: string
    disclosure_url: string
  }
  features: Record<string, boolean>
}

export interface DisclaimerAck {
  acknowledged_version: string
  current_version: string
  required: boolean
}

export type ComplianceFeature =
  | 'suggestion_pool'
  | 'prediction_tracking'
  | 'entry_candidates'
  | 'strategy_signals'
  | 'evaluations'
  | 'ai_paper_trading'
  | 'position_calculator'
  | 'share_cards'
  | 'tradingagents_rating'
  | 'mcp_server'

/** Shown until the server status loads, and if it cannot be loaded. */
export const FALLBACK_SHORT_DISCLAIMER =
  'Educational/informational only. Not a SEBI-registered investment adviser or research analyst. ' +
  'Not investment advice. AI can be wrong. Markets carry risk.'

export const RESTRICTED_PREFIX = 'ADVISORY_MODE_RESTRICTED'

export function isRestrictedError(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith(RESTRICTED_PREFIX)
}

export const complianceApi = {
  status: () => fetchAPI<ComplianceStatus>('/compliance/status'),
  getAck: () => fetchAPI<DisclaimerAck>('/compliance/ack'),
  acknowledge: (version: string) =>
    fetchAPI<DisclaimerAck>('/compliance/ack', {
      method: 'POST',
      body: JSON.stringify({ version }),
    }),
}
