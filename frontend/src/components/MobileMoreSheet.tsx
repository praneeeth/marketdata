import { NavLink } from 'react-router-dom'
import { Github, ScrollText } from 'lucide-react'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@candlewise/base-ui/components/ui/dialog'
import type { NavItem } from '@/router/nav-items'
import { preloadRoute } from '@/router/page-loaders'
import { SimulationBadge } from '@/components/common/Brand'

/** Phone "More" sheet: every page that doesn't fit in the tab bar, plus logs and the project link. */
export default function MobileMoreSheet({
  open,
  onOpenChange,
  items,
  onOpenLogs,
  repoUrl,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  items: NavItem[]
  onOpenLogs: () => void
  repoUrl: string
}) {
  const close = () => onOpenChange(false)
  const rowCls =
    'flex min-h-[48px] w-full items-center gap-3 rounded-xl px-3 text-[14px] text-foreground hover:bg-accent active:bg-accent'
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent aria-describedby="more-sheet-desc">
        <DialogHeader className="mb-3">
          <DialogTitle>More</DialogTitle>
          <DialogDescription id="more-sheet-desc">Everything else in Candlewise.</DialogDescription>
        </DialogHeader>
        <nav aria-label="More pages" className="grid gap-0.5">
          {items.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={close}
              onFocus={() => preloadRoute(to)}
              className={({ isActive }) => `${rowCls} ${isActive ? 'bg-accent font-semibold' : ''}`}
            >
              <Icon className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
              <span className="flex-1">{label}</span>
              {to === '/paper-trading' && <SimulationBadge />}
            </NavLink>
          ))}
          <div className="my-2 border-t border-border/70" />
          <button type="button" className={rowCls} onClick={() => { close(); onOpenLogs() }}>
            <ScrollText className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            View logs
          </button>
          <a href={repoUrl} target="_blank" rel="noopener noreferrer" className={rowCls} onClick={close}>
            <Github className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            Project on GitHub
          </a>
        </nav>
      </DialogContent>
    </Dialog>
  )
}
