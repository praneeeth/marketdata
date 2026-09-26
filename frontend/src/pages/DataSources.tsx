import { Globe } from 'lucide-react'
import BrokerConnections from '@/components/BrokerConnections'
import { PageHeader } from '@/components/common/Brand'

/**
 * Data sources (India-only). Indian market data comes from the user's own broker; global
 * cues come from a free, delayed source until a licensed feed replaces it. The upstream
 * Chinese vendor configuration (Tencent, Eastmoney, Xueqiu, ...) was removed in Phase 2.
 */
export default function DataSourcesPage() {
  return (
    <div>
      <PageHeader
        eyebrow="Settings"
        title="Data sources"
        description="NSE / BSE prices, charts and instrument lists come from your own broker account. Read-only: Candlewise never places orders."
      />

      <div className="space-y-6">
        <BrokerConnections />

        <section className="card p-4 space-y-1" data-testid="global-cues-source">
          <div className="flex items-center gap-2">
            <Globe className="w-4 h-4 text-muted-foreground" aria-hidden="true" />
            <h2 className="font-display text-[16px] font-semibold text-foreground">Global markets</h2>
            <span className="rounded border border-note-border bg-note px-1.5 py-0.5 text-[10px] text-note-foreground">
              Delayed / unofficial
            </span>
          </div>
          <p className="text-[12px] text-muted-foreground">
            World indices, Brent crude, gold and USD/INR on the dashboard come from free, delayed Yahoo
            data, for context only. They will move to a licensed feed before a public launch. The server
            setting <code>GLOBAL_CUES_SOURCE=off</code> hides them.
          </p>
        </section>

        <section className="card p-4 space-y-1">
          <h2 className="font-display text-[16px] font-semibold text-foreground">News and filings</h2>
          <p className="text-[12px] text-muted-foreground">
            Indian news, NSE/BSE announcements and fundamentals are coming in a later update. Until then,
            agents and the assistant report "no news" instead of guessing.
          </p>
        </section>
      </div>
    </div>
  )
}
