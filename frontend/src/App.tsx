import { Suspense, useState, useEffect, useRef } from 'react'
import { Routes, Route, NavLink, useLocation, Navigate } from 'react-router-dom'
import { ScrollText, Github, MoreHorizontal } from 'lucide-react'
import { useTheme } from '@/hooks/use-theme'
import { CandlewiseLogo } from '@/components/CandlewiseLogo'
import { RELEASES_URL, REPO_URL } from '@/lib/brand'
import { appApi } from '@candlewise/api/app'
import { fetchAPI, isAuthenticated } from '@candlewise/api/client'
import LogsModal from '@candlewise/biz-ui/components/logs-modal'
import MobileMoreSheet from '@/components/MobileMoreSheet'
import AccountMenu from '@/components/AccountMenu'
import AssistantOpenBridge from '@/components/AssistantOpenBridge'
import SelfCheckModal from '@/components/SelfCheckModal'
import { RouteErrorBoundary, RouteLoadingFallback } from '@/components/RouteBoundary'
import { preloadRoute, routePages } from '@/router/page-loaders'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@candlewise/base-ui/components/ui/dialog'
import { Button } from '@candlewise/base-ui/components/ui/button'
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
  StockDetailPage,
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
  const mobilePrimaryNavItems = visibleNavItems.slice(0, 4)
  const mobileMoreNavItems = visibleNavItems.slice(4)
  const [moreOpen, setMoreOpen] = useState(false)
  const moreActive = mobileMoreNavItems.some(({ to }) => location.pathname.startsWith(to))
  const isAssistantRoute = location.pathname === '/assistant' || location.pathname.startsWith('/assistant/')
  const [version, setVersion] = useState('')
  const [logsOpen, setLogsOpen] = useState(false)
  const [selfCheckOpen, setSelfCheckOpen] = useState(false)
  const [upgradeOpen, setUpgradeOpen] = useState(false)
  const [upgradeInfo, setUpgradeInfo] = useState<{ latest: string; url: string } | null>(null)
  const checkedUpdateRef = useRef(false)
  const repoUrl = REPO_URL

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
        const dismissed = localStorage.getItem('candlewise_upgrade_dismissed_version') || ''
        if (dismissed === latest) return
        setUpgradeInfo({ latest, url: String(res?.release_url || RELEASES_URL) })
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
        ? 'relative flex h-dvh flex-col overflow-hidden bg-background pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0'
        : 'relative min-h-screen overflow-x-clip bg-background pb-[calc(3.5rem+env(safe-area-inset-bottom))] md:pb-0'}
    >
      {/* Desktop top bar */}
      <div className="sticky top-0 z-40 hidden border-b border-border/70 bg-background/90 backdrop-blur md:block">
        <header className="mx-auto flex h-14 w-full max-w-6xl items-center gap-6 px-6">
          <NavLink to="/" className="flex shrink-0 items-center gap-2" aria-label="Candlewise home">
            <CandlewiseLogo markClassName="w-7 h-7" wordmarkClassName="font-display text-[17px] font-semibold tracking-tight" />
            {version && <span className="text-[11px] text-muted-foreground/70">v{version}</span>}
          </NavLink>

          <nav aria-label="Main" className="flex flex-1 items-center gap-1">
            {desktopPrimaryNavItems.map(({ to, label }) => {
              const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
              return (
                <NavLink
                  key={to}
                  to={to}
                  aria-current={isActive ? 'page' : undefined}
                  onMouseEnter={() => preloadRoute(to)}
                  onFocus={() => preloadRoute(to)}
                  className={`relative px-3 py-2 text-[13px] font-medium transition-colors ${
                    isActive ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  {label}
                  <span
                    aria-hidden="true"
                    className={`absolute inset-x-3 -bottom-[11px] h-0.5 rounded-full ${isActive ? 'bg-primary' : 'bg-transparent'}`}
                  />
                </NavLink>
              )
            })}
          </nav>

          <div className="flex items-center gap-1">
            <button
              onClick={() => window.open(repoUrl, '_blank', 'noopener,noreferrer')}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              title="Project on GitHub"
              aria-label="Project on GitHub"
            >
              <Github className="h-4 w-4" />
            </button>
            <button
              onClick={() => setLogsOpen(true)}
              className="flex h-9 w-9 items-center justify-center rounded-lg text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              title="View logs"
              aria-label="View logs"
            >
              <ScrollText className="h-4 w-4" />
            </button>
            <AccountMenu
              navItems={desktopMoreNavItems}
              mode={mode}
              onSetMode={setMode}
              onOpenSelfCheck={() => setSelfCheckOpen(true)}
            />
          </div>
        </header>
      </div>

      {/* Phone top bar */}
      <div className="sticky top-0 z-40 border-b border-border/70 bg-background/90 pt-[env(safe-area-inset-top)] backdrop-blur md:hidden">
        <header className="flex h-12 items-center justify-between px-4">
          <NavLink to="/" className="flex min-w-0 items-center" aria-label="Candlewise home">
            <CandlewiseLogo markClassName="w-6 h-6" wordmarkClassName="font-display text-[16px] font-semibold tracking-tight" />
          </NavLink>
          <AccountMenu
            size="sm"
            navItems={mobileMoreNavItems}
            mode={mode}
            onSetMode={setMode}
            onOpenSelfCheck={() => setSelfCheckOpen(true)}
          />
        </header>
      </div>

      {/* Phone tab bar: four pages + More */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 border-t border-border/70 bg-background/95 pb-[env(safe-area-inset-bottom)] backdrop-blur md:hidden"
      >
        <div className="mx-auto grid h-14 max-w-md grid-cols-5">
          {mobilePrimaryNavItems.map(({ to, icon: Icon, label }) => {
            const isActive = to === '/' ? location.pathname === '/' : location.pathname.startsWith(to)
            return (
              <NavLink
                key={to}
                to={to}
                aria-current={isActive ? 'page' : undefined}
                onFocus={() => preloadRoute(to)}
                className={`relative flex flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
                  isActive ? 'text-primary' : 'text-muted-foreground'
                }`}
              >
                <span aria-hidden="true" className={`absolute top-0 h-0.5 w-8 rounded-full ${isActive ? 'bg-primary' : 'bg-transparent'}`} />
                <Icon className="h-5 w-5" aria-hidden="true" />
                {label}
              </NavLink>
            )
          })}
          <button
            type="button"
            onClick={() => setMoreOpen(true)}
            aria-haspopup="dialog"
            className={`relative flex flex-col items-center justify-center gap-0.5 text-[11px] font-medium ${
              moreActive ? 'text-primary' : 'text-muted-foreground'
            }`}
          >
            <span aria-hidden="true" className={`absolute top-0 h-0.5 w-8 rounded-full ${moreActive ? 'bg-primary' : 'bg-transparent'}`} />
            <MoreHorizontal className="h-5 w-5" aria-hidden="true" />
            More
          </button>
        </div>
      </nav>
      <MobileMoreSheet
        open={moreOpen}
        onOpenChange={setMoreOpen}
        items={mobileMoreNavItems}
        onOpenLogs={() => setLogsOpen(true)}
        repoUrl={repoUrl}
      />

      {/* Content */}
      <main
        className={`${isAssistantRoute ? 'flex min-h-0 flex-1 flex-col overflow-hidden' : 'mx-auto max-w-6xl'} w-full min-w-0 px-4 py-4 md:px-6 md:py-8`}
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
              <Route path="/stock/:symbol" element={<StockDetailPage />} />
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
                if (upgradeInfo?.latest) localStorage.setItem('candlewise_upgrade_dismissed_version', upgradeInfo.latest)
                setUpgradeOpen(false)
              }}
            >
              Remind me later
            </Button>
            <Button
              onClick={() => {
                const url = upgradeInfo?.url || RELEASES_URL
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
