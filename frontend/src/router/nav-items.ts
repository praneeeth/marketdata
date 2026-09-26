import { Bot, Settings, List, Database, Clock, LayoutDashboard, BellRing, Sparkles, Activity, ClipboardCheck, MessageCircle } from 'lucide-react'
import type { ComplianceFeature } from '@candlewise/api/compliance'

export interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  /** Hidden unless this compliance feature is enabled (research-only hides it). */
  feature?: ComplianceFeature
}

export const navItems: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: 'Home' },
  { to: '/portfolio', icon: List, label: 'Holdings' },
  { to: '/opportunities', icon: Sparkles, label: 'Opportunities', feature: 'entry_candidates' },
  { to: '/paper-trading', icon: Activity, label: 'Simulation' },
  { to: '/assistant', icon: MessageCircle, label: 'Assistant' },
  { to: '/alerts', icon: BellRing, label: 'Alerts' },
  { to: '/agents', icon: Bot, label: 'Agent' },
  { to: '/evaluations', icon: ClipboardCheck, label: 'Evaluations', feature: 'evaluations' },
  { to: '/history', icon: Clock, label: 'History' },
  { to: '/datasources', icon: Database, label: 'Data sources' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export function visibleNavItems(isEnabled: (feature: ComplianceFeature) => boolean): NavItem[] {
  return navItems.filter((item) => !item.feature || isEnabled(item.feature))
}
