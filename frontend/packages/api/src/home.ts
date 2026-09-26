import { fetchAPI } from './client'

export interface AlertHitToday {
  rule_id: number
  rule_name: string
  symbol: string
  name: string
  market: string
  trigger_time: string
  snapshot: Record<string, unknown>
}

export interface PortfolioTodo {
  type: string // no_alert | alert_expiring
  symbol?: string
  market?: string
  message: string
}

export const homeApi = {
  /** All of today's alert triggers (local time zone), across rules. */
  alertHitsToday: () => fetchAPI<AlertHitToday[]>('/price-alerts/hits/today'),

  /** Home page empty-state to-dos: held without an alert / alert about to expire. */
  todos: () => fetchAPI<{ todos: PortfolioTodo[]; count: number }>('/portfolio/todos'),
}
