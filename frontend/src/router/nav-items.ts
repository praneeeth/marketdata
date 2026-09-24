import { Bot, Settings, List, Database, Clock, LayoutDashboard, BellRing, Sparkles, Activity, ClipboardCheck, MessageCircle } from 'lucide-react'
import type { ComplianceFeature } from '@panwatch/api/compliance'

export interface NavItem {
  to: string
  icon: typeof LayoutDashboard
  label: string
  /** Hidden unless this compliance feature is enabled (research-only hides it). */
  feature?: ComplianceFeature
}

export const navItems: NavItem[] = [
  { to: '/', icon: LayoutDashboard, label: '首页' },
  { to: '/portfolio', icon: List, label: '持仓' },
  { to: '/opportunities', icon: Sparkles, label: '机会', feature: 'entry_candidates' },
  { to: '/paper-trading', icon: Activity, label: 'Simulation' },
  { to: '/assistant', icon: MessageCircle, label: '助手' },
  { to: '/alerts', icon: BellRing, label: '提醒' },
  { to: '/agents', icon: Bot, label: 'Agent' },
  { to: '/evaluations', icon: ClipboardCheck, label: '验证中心', feature: 'evaluations' },
  { to: '/history', icon: Clock, label: '历史' },
  { to: '/datasources', icon: Database, label: '数据源' },
  { to: '/settings', icon: Settings, label: '设置' },
]

export function visibleNavItems(isEnabled: (feature: ComplianceFeature) => boolean): NavItem[] {
  return navItems.filter((item) => !item.feature || isEnabled(item.feature))
}
