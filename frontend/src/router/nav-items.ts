import { Bot, Settings, List, Database, Clock, LayoutDashboard, BellRing, Sparkles, Activity, ClipboardCheck, MessageCircle } from 'lucide-react'
import type { ComplianceFeature } from '@candlewise/api/compliance'

export interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  /** Hidden unless this compliance feature is enabled (research-only hides it). */
  feature?: ComplianceFeature
}

/**
 * Order matters: the first four visible items are the phone tab bar (plus "More"), the first
 * five are the desktop bar, and the rest live under "More" / the account menu.
 */
export const navItems: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: 'Home' },
  { to: '/portfolio', icon: List, label: 'Portfolio' },
  { to: '/assistant', icon: MessageCircle, label: 'Research' },
  { to: '/alerts', icon: BellRing, label: 'Alerts' },
  { to: '/paper-trading', icon: Activity, label: 'Simulation' },
  { to: '/opportunities', icon: Sparkles, label: 'Opportunities', feature: 'entry_candidates' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/evaluations', icon: ClipboardCheck, label: 'Evaluations', feature: 'evaluations' },
  { to: '/history', icon: Clock, label: 'History' },
  { to: '/datasources', icon: Database, label: 'Data sources' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export function visibleNavItems(isEnabled: (feature: ComplianceFeature) => boolean): NavItem[] {
  return navItems.filter((item) => !item.feature || isEnabled(item.feature))
}
