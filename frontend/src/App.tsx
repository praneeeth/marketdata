import { Suspense, useState, useEffect, useRef } from 'react'
import { Routes, Route, NavLink, useLocation, Navigate } from 'react-router-dom'
import { TrendingUp, ScrollText, Github } from 'lucide-react'
import { useTheme } from '@/hooks/use-theme'
import { appApi } from '@panwatch/api/app'
import { fetchAPI, isAuthenticated } from '@panwatch/api/client'
import LogsModal from '@panwatch/biz-ui/components/logs-modal'
import AmbientBackground from '@panwatch/biz-ui/components/AmbientBackground'
import AccountMenu from '@/components/AccountMenu'
import AssistantOpenBridge from '@/components/AssistantOpenBridge'
import SelfCheckModal from '@/components/SelfCheckModal'
import { RouteErrorBoundary, RouteLoadingFallback } from '@/components/RouteBoundary'
import { preloadRoute, routePages } from '@/router/page-loaders'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@panwatch/base-ui/components/ui/dialog'
import { Button } from '@panwatch/base-ui/components/ui/button'
import DisclaimerFooter from '@/components/DisclaimerFooter'
import DisclaimerConsentDialog from '@/components/DisclaimerConsentDialog'
import { useCompliance } from '@/hooks/use-compliance'
import { visibleNavItems as filterNavItems } from '@/router/nav-items'

const {
  LoginPage,
  DashboardPage,
  OpportunitiesPage,
  StocksPage,
  AgentsPage,
  SettingsPage,
  DataSourcesPage,
  HistoryPage,
  AnalysisDetailPage,
  PriceAlertsPage,
  PaperTradingPage,
  EvaluationsPage,
  AssistantPage,
} = routePages

// Auth guard component
function RequireAuth({ children }: { children: React.ReactNode }) {
  const [authState, setAuthState] = useState<'checking' | 'authenticated' | 'unauthenticated'>('checking')
  const location = useLocation()

  useEffect(() => {
    // Check for a local token
    if (isAuthenticated()) {
      setAuthState('authenticated')
      return
    }

    // No token: go to the login page (set a password or sign in)
    setAuthState('unauthenticated')
  }, [])

  if (authState === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="w-6 h-6 border-2 border-primary/30 border-t-primary rounded-full animate-spin" />
      </div>
    )
  }

  if (authState === 'unauthenticated') {
    return <Navigate to="/login" state={{ from: location }} replace />
  }

  return <>{children}</>
}

function App() {
  const { mode, setMode } = useTheme()
  const location = useLocation()
  const { isEnabled } = useCompliance()
  const visibleNavItems = filterNavItems(isEnabled)
  const desktopPrimaryNavItems = visibleNavItems.slice(0, 5)
  const desktopMoreNavItems = visibleNavItems.slice(5)
  const mobilePrimaryNavItems = visibleNavItems.slice(0, 5)
  const mobileMoreNavItems = visibleNavItems.slice(5)
  const isAssistantRoute = location.pathname === '/assistant' || location.pathname.startsWith('/assistant/')
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [selfCheckOpen, setSelfCheckOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const checkedUpdateRef = useRef(false)
  const repoUrl = 'https://github.com/TNT-Likely/PanWatch'

  useEffect(() => {
    appApi.version()
      .then(data => setVersion(data?.version || ''))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (checkedUpdateRef.current) return
    if (!isAuthenticated()) return
    const current = String(version || '').trim()
    if (!current || current === 'dev') return
    checkedUpdateRef.current = true

    fetchAPI<any>('/settings/update-check')
      .then((res) => {
        const latest = String(res?.latest_version || '').trim()
        const shouldOpen = !!res?.update_available && !!latest
        if (!shouldOpen) return
        const dismissed = localStorage.getItem('panwatch_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || 'https://github.com/sunxiao0721/PanWatch/releases') })
        setUpgradeOpen(true)
      })
      .catch(() => {})
  }, [version])

  // No navigation on the login page
  if (location.pathname === '/login') {
    return (
      <RouteErrorBoundary>
        <Suspense fallback={<RouteLoadingFallback />}>
          <Routes>
            <Route path="/login" element={<LoginPage />} />
          </Routes>
        </Suspense>
        <DisclaimerFooter />
      </RouteErrorBoundary>
    )
  }

  return (
    <RequireAuth>
    <div
      className={isAssistantRoute
        ? 'relative flex h-dvh flex-col overflow-hidden bg-background pb-16 md:pb-0'
        : 'min-h-screen pb-16 md:pb-0 relative overflow-x-clip bg-background'}
    >
      <AmbientBackground />
      {/* Desktop Floating Nav */}
      <div className="sticky top-0 z-50 px-4 md:px-6 pt-3 md:pt-4 pb-2 hidden md:block">
        <header className="card px-4 md:px-5">
          <div className="h-14 flex items-center justify-between">
            {/* Logo */}
            <NavLink to="/" className="flex items-center gap-2.5 group">
              <div className="w-8 h-8 rounded-2xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-4 h-4 text-white" />
              </div>
              <span className="text-[15px] font-bold text-foreground">PanWatch</span>
              {version && <span className="text-[11px] text-muted-foreground/60 font-normal">v{version}</span>}
            </NavLink>

            {/* Nav Links */}
            <nav className="flex items-center gap-1">
              {desktopPrimaryNavItems.map(({ to, icon: Icon, label }) => {
                const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
                return (
                  <NavLink
                    key={to}
                    to={to}
                    className="relative"
                    onMouseEnter={() => preloadRoute(to)}
                    onFocus={() => preloadRoute(to)}
                  >
                    <span
                      className={`absolute inset-0 rounded-xl transition-all ${
                        isActive
                          ? 'bg-[linear-gradient(135deg,hsl(var(--primary)/0.14),hsl(var(--primary)/0.04),hsl(var(--success)/0.06))] ring-1 ring-primary/20 shadow-[0_8px_24px_-18px_hsl(var(--primary)/0.55)]'
                          : 'bg-transparent'
                      }`}
                    />
                    <span
                      className={`relative px-3.5 py-2 rounded-xl text-[13px] font-medium transition-all flex items-center gap-1.5 ${
                        isActive
                          ? 'text-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                      }`}
                    >
                      <Icon className={`w-4 h-4 ${isActive ? 'text-primary' : ''}`} />
                      {label}
                    </span>
                  </NavLink>
                )
              })}
            </nav>

            {/* action wrapper: GitHub + logs + avatar (the avatar dropdown has more navigation/theme/sign-out) */}
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="GitHub project"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-9 h-9 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="View logs"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <AccountMenu
                navItems={desktopMoreNavItems}
                mode={mode}
                onSetMode={setMode}
                onOpenSelfCheck={() => setSelfCheckOpen(true)}
              />
            </div>
          </div>
        </header>
      </div>

      {/* Mobile Top Bar */}
      <div className="sticky top-0 z-50 px-4 pt-[max(0.75rem,env(safe-area-inset-top))] pb-2 md:hidden">
        <header className="card px-4">
          <div className="h-12 flex items-center justify-between">
            <NavLink to="/" className="flex items-center gap-2 group">
              <div className="w-7 h-7 rounded-xl bg-gradient-to-br from-primary to-primary/70 flex items-center justify-center shadow-sm">
                <TrendingUp className="w-3.5 h-3.5 text-white" />
              </div>
              <span className="text-[14px] font-bold text-foreground">PanWatch</span>
              {version && <span className="text-[10px] text-muted-foreground/60 font-normal">v{version}</span>}
            </NavLink>
            <div className="flex items-center gap-1.5 px-1.5 py-1 rounded-2xl bg-accent/20 border border-border/40">
              <button
                onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="GitHub project"
              >
                <Github className="w-4 h-4" />
              </button>
              <button
                onClick={() => setLogsOpen(true)}
                className="w-8 h-8 rounded-xl flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-background/70 transition-all"
                title="View logs"
              >
                <ScrollText className="w-4 h-4" />
              </button>
              <AccountMenu
                size="sm"
                navItems={mobileMoreNavItems}
                mode={mode}
                onSetMode={setMode}
                onOpenSelfCheck={() => setSelfCheckOpen(true)}
              />
            </div>
          </div>
        </header>
      </div>

      {/* Mobile Bottom Nav */}
      <nav className="fixed bottom-0 left-0 right-0 z-50 md:hidden bg-card border-t border-border px-2 pb-[env(safe-area-inset-bottom)]">
        <div className="flex items-center justify-around h-14">
          {mobilePrimaryNavItems.map(({ to, icon: Icon, label }) => {
            const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                onMouseEnter={() => preloadRoute(to)}
                onFocus={() => preloadRoute(to)}
                className={`flex flex-col items-center justify-center gap-0.5 px-2 py-1.5 rounded-xl transition-all min-w-[56px] ${
                  isActive
                    ? 'text-primary bg-primary/8 ring-1 ring-primary/15'
                    : 'text-muted-foreground hover:bg-accent/30'
                }`}
              >
                <Icon className="w-5 h-5" />
                <span className="text-[10px] font-medium">{label}</span>
              </NavLink>
            )
          })}
        </div>
      </nav>

      {/* Content */}
      <main
        className={`${isAssistantRoute ? 'flex min-h-0 flex-1 flex-col overflow-hidden' : ''} px-4 md:px-6 py-4 md:py-6 w-full`}
      >
        <AssistantOpenBridge />
        <RouteErrorBoundary>
          <Suspense fallback={<RouteLoadingFallback />}>
            <Routes>
              <Route path="/" element={<DashboardPage />} />
              <Route
                path="/opportunities"
                element={isEnabled('entry_candidates') ? <OpportunitiesPage /> : <Navigate to="/" replace />}
              />
              <Route path="/portfolio" element={<StocksPage />} />
              <Route path="/agents" element={<AgentsPage />} />
              <Route
                path="/evaluations"
                element={isEnabled('evaluations') ? <EvaluationsPage /> : <Navigate to="/" replace />}
              />
              <Route path="/history" element={<HistoryPage />} />
              <Route path="/paper-trading" element={<PaperTradingPage />} />
              <Route path="/alerts" element={<PriceAlertsPage />} />
              <Route path="/assistant" element={<AssistantPage />} />
              <Route path="/assistant/:conversationId" element={<AssistantPage />} />
              <Route path="/datasources" element={<DataSourcesPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              <Route path="/analysis/:symbol/:date" element={<AnalysisDetailPage />} />
            </Routes>
          </Suspense>
        </RouteErrorBoundary>
        {!isAssistantRoute && <DisclaimerFooter />}
      </main>
      {isAssistantRoute && <DisclaimerFooter className="py-1" />}
      <DisclaimerConsentDialog />
      <LogsModal open={logsOpen} onOpenChange={setLogsOpen} />
      <SelfCheckModal open={selfCheckOpen} onClose={() => setSelfCheckOpen(false)} />
      <Dialog open={upgradeOpen} onOpenChange={setUpgradeOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>New version available</DialogTitle>
            <DialogDescription>
              You're on v{version}; v{upgradeInfo?.latest} is available.
            </DialogDescription>
          </DialogHeader>
          <div className="text-[12px] text-muted-foreground">
            Upgrading is recommended for the latest features and fixes.
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="secondary"
              onClick={() => {
                if (upgradeInfo?.latest) localStorage.setItem('panwatch_upgrade_dismissed_version', upgradeInfo.latest)
                setUpgradeOpen(false)
              }}
            >
              Remind me later
            </Button>
            <Button
              onClick={() => {
                const url = upgradeInfo?.url || 'https://github.com/sunxiao0721/PanWatch/releases'
                window.open(url, '_blank', 'noopener,noreferrer')
              }}
            >
              Upgrade
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
    </RequireAuth>
  )
}

export default App
