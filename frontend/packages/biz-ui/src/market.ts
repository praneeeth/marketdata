export interface MarketBadgeInfo {
  style: string
  label: string
}

export function getMarketBadge(market: string): MarketBadgeInfo {
  // India is the only market.
  return { style: 'bg-violet-500/10 text-violet-600', label: market === 'IN' || !market ? 'NSE/BSE' : market }
}
