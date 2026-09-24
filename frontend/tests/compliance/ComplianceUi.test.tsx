import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('@panwatch/api/compliance', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@panwatch/api/compliance')>()
  return {
    ...actual,
    complianceApi: { status: vi.fn(), getAck: vi.fn(), acknowledge: vi.fn() },
  }
})

import {
  complianceApi,
  FALLBACK_SHORT_DISCLAIMER,
  type ComplianceFeature,
  type ComplianceStatus,
} from '@panwatch/api/compliance'
import { ComplianceProvider, useCompliance } from '@/hooks/use-compliance'
import DisclaimerFooter from '@/components/DisclaimerFooter'
import DisclaimerConsentDialog from '@/components/DisclaimerConsentDialog'
import ShareCardDialog from '@/components/ShareCardDialog'
import { navItems, visibleNavItems } from '@/router/nav-items'
import { SuggestionBadge, type KlineSummary, type SuggestionInfo } from '@panwatch/biz-ui/components/suggestion-badge'
import { KlineSummaryDialog } from '@panwatch/biz-ui/components/kline-summary-dialog'
import AddPositionCalculator from '@panwatch/biz-ui/components/add-position-calculator'
import { Onboarding } from '@panwatch/biz-ui/components/onboarding'
import { buildAnalysisSections } from '@panwatch/biz-ui/analysis-sections'

const SERVER_SHORT = 'Server short disclaimer: not investment advice.'
const ALL_FEATURES: ComplianceFeature[] = [
  'suggestion_pool',
  'prediction_tracking',
  'entry_candidates',
  'strategy_signals',
  'evaluations',
  'ai_paper_trading',
  'position_calculator',
  'share_cards',
  'tradingagents_rating',
  'mcp_server',
]

function statusWith(enabled: ComplianceFeature[] = []): ComplianceStatus {
  return {
    mode: 'research_only',
    disclaimer: { version: '2026-09-24.1', short: SERVER_SHORT, long: 'Long disclaimer text.' },
    simulation: { label: 'Simulation', notice: 'Simulated trades only.' },
    research_analyst: null,
    features: Object.fromEntries(ALL_FEATURES.map((f) => [f, enabled.includes(f)])),
  }
}

/** Renders inside the provider and waits until the server status has been applied. */
async function renderLoaded(ui: ReactNode, enabled: ComplianceFeature[] = []) {
  vi.mocked(complianceApi.status).mockResolvedValue(statusWith(enabled))
  const view = render(
    <MemoryRouter>
      <ComplianceProvider>
        {ui}
        <DisclaimerFooter />
      </ComplianceProvider>
    </MemoryRouter>,
  )
  await waitFor(() => expect(screen.getByTestId('disclaimer-footer').textContent).toBe(SERVER_SHORT))
  return view
}

const KLINE: KlineSummary = {
  trend: '多头排列',
  macd_status: '金叉',
  recent_5_up: 4,
  change_5d: 3.2,
  change_20d: 8.1,
  ma5: 10,
  ma10: 9.5,
  ma20: 9,
  rsi6: 65,
  rsi_status: '偏强',
  support: 9.2,
  resistance: 11.5,
}

const AI_SUGGESTION: SuggestionInfo = {
  id: 7,
  action: 'buy',
  action_label: '买入',
  signal: 'Breakout',
  reason: 'Momentum',
  should_alert: true,
  agent_name: 'daily_report',
  agent_label: '盘后日报',
}

beforeEach(() => {
  vi.mocked(complianceApi.status).mockReset()
  vi.mocked(complianceApi.getAck).mockReset()
  vi.mocked(complianceApi.acknowledge).mockReset()
  vi.mocked(complianceApi.getAck).mockResolvedValue({
    acknowledged_version: '2026-09-24.1',
    current_version: '2026-09-24.1',
    required: false,
  })
})

describe('compliance provider', () => {
  it('shows the fallback disclaimer and fails closed when status cannot load', async () => {
    vi.mocked(complianceApi.status).mockRejectedValue(new Error('offline'))
    let probe: ((f: ComplianceFeature) => boolean) | null = null
    function Probe() {
      probe = useCompliance().isEnabled
      return null
    }
    render(
      <ComplianceProvider>
        <Probe />
        <DisclaimerFooter />
      </ComplianceProvider>,
    )
    await waitFor(() => expect(complianceApi.status).toHaveBeenCalled())
    expect(screen.getByTestId('disclaimer-footer').textContent).toBe(FALLBACK_SHORT_DISCLAIMER)
    for (const feature of ALL_FEATURES) expect(probe!(feature)).toBe(false)
  })

  it('footer shows the server disclaimer once loaded', async () => {
    await renderLoaded(null)
    expect(screen.getByTestId('disclaimer-footer').textContent).toBe(SERVER_SHORT)
  })

  it('fallback text carries every required element', () => {
    for (const phrase of [
      'Educational/informational only',
      'Not a SEBI-registered investment adviser or research analyst',
      'Not investment advice',
      'AI can be wrong',
      'Markets carry risk',
    ]) {
      expect(FALLBACK_SHORT_DISCLAIMER).toContain(phrase)
    }
  })
})

describe('disclaimer consent', () => {
  it('blocks until the checkbox is ticked, then records the current version', async () => {
    vi.mocked(complianceApi.getAck).mockResolvedValue({
      acknowledged_version: '',
      current_version: '2026-09-24.1',
      required: true,
    })
    vi.mocked(complianceApi.acknowledge).mockResolvedValue({
      acknowledged_version: '2026-09-24.1',
      current_version: '2026-09-24.1',
      required: false,
    })
    const user = userEvent.setup()
    await renderLoaded(<DisclaimerConsentDialog />)

    const dialog = await screen.findByTestId('disclaimer-consent')
    expect(dialog.textContent).toContain('Long disclaimer text.')
    const accept = screen.getByRole('button', { name: 'I understand' }) as HTMLButtonElement
    expect(accept.disabled).toBe(true)

    await user.click(screen.getByLabelText('I understand this is not investment advice'))
    expect(accept.disabled).toBe(false)
    await user.click(accept)

    expect(complianceApi.acknowledge).toHaveBeenCalledWith('2026-09-24.1')
    await waitFor(() => expect(screen.queryByTestId('disclaimer-consent')).toBeNull())
  })

  it('opens when the acknowledgement state cannot be read', async () => {
    vi.mocked(complianceApi.getAck).mockRejectedValue(new Error('offline'))
    await renderLoaded(<DisclaimerConsentDialog />)
    expect(await screen.findByTestId('disclaimer-consent')).toBeTruthy()
  })

  it('stays closed when the current version is already acknowledged', async () => {
    await renderLoaded(<DisclaimerConsentDialog />)
    await waitFor(() => expect(complianceApi.getAck).toHaveBeenCalled())
    expect(screen.queryByTestId('disclaimer-consent')).toBeNull()
  })
})

describe('navigation gating', () => {
  it('hides recommendation pages in research-only mode', () => {
    const paths = visibleNavItems(() => false).map((i) => i.to)
    expect(paths).not.toContain('/opportunities')
    expect(paths).not.toContain('/evaluations')
    expect(paths).toContain('/paper-trading')
  })

  it('shows them when the features are enabled', () => {
    expect(visibleNavItems(() => true)).toHaveLength(navItems.length)
  })

  it('labels paper trading as a simulation', () => {
    expect(navItems.find((i) => i.to === '/paper-trading')?.label).toBe('Simulation')
  })
})

describe('share cards', () => {
  it('replace the card with a notice in research-only mode', async () => {
    await renderLoaded(
      <ShareCardDialog open onClose={() => {}} filename="card">
        <div>Card body with a Buy call</div>
      </ShareCardDialog>,
    )
    expect(await screen.findByTestId('sharing-disabled')).toBeTruthy()
    expect(screen.queryByText('Card body with a Buy call')).toBeNull()
  })

  it('render the card when sharing is enabled', async () => {
    await renderLoaded(
      <ShareCardDialog open onClose={() => {}} filename="card">
        <div>Card body</div>
      </ShareCardDialog>,
      ['share_cards'],
    )
    expect(await screen.findByText('Card body')).toBeTruthy()
  })
})

describe('action badges', () => {
  it('show only the neutral indicators badge in research-only mode', async () => {
    await renderLoaded(
      <SuggestionBadge suggestion={AI_SUGGESTION} kline={KLINE} stockSymbol="INFY" stockName="Infosys" />,
    )
    expect(screen.getByText('指标')).toBeTruthy()
    for (const label of ['买入', '加仓', '持有', '卖出', '减仓', '观望', '回避']) {
      expect(screen.queryByText(label)).toBeNull()
    }
  })

  it('render nothing in research-only mode without indicators', async () => {
    const { container } = await renderLoaded(
      <div data-testid="badge-host">
        <SuggestionBadge suggestion={AI_SUGGESTION} kline={null} showFullInline />
      </div>,
    )
    expect(container.querySelector('[data-testid="badge-host"]')?.textContent).toBe('')
  })

  it('show the action when the suggestion pool is enabled', async () => {
    await renderLoaded(<SuggestionBadge suggestion={AI_SUGGESTION} kline={KLINE} />, ['suggestion_pool'])
    expect(screen.getAllByText('买入').length).toBeGreaterThan(0)
  })
})

describe('K-line dialog', () => {
  it('omits the rule-based action and scoring rules in research-only mode', async () => {
    await renderLoaded(
      <KlineSummaryDialog open onOpenChange={() => {}} symbol="INFY" market="IN" initialSummary={KLINE} />,
    )
    expect(await screen.findByText('K线 / 技术指标')).toBeTruthy()
    expect(screen.queryByText(/score/)).toBeNull()
    expect(screen.queryByText(/建议\/评分规则说明/)).toBeNull()
  })

  it('shows the scoring when the suggestion pool is enabled', async () => {
    await renderLoaded(
      <KlineSummaryDialog open onOpenChange={() => {}} symbol="INFY" market="IN" initialSummary={KLINE} />,
      ['suggestion_pool'],
    )
    expect(await screen.findByText(/建议\/评分规则说明/)).toBeTruthy()
  })
})

describe('position calculator', () => {
  it('is not rendered in research-only mode', async () => {
    await renderLoaded(
      <div data-testid="calc-host">
        <AddPositionCalculator symbol="INFY" market="IN" currentQuantity={10} currentCost={1500} currentPrice={1520} />
      </div>,
    )
    expect(screen.getByTestId('calc-host').textContent).toBe('')
  })

  it('is rendered when enabled', async () => {
    await renderLoaded(
      <div data-testid="calc-host">
        <AddPositionCalculator symbol="INFY" market="IN" currentQuantity={10} currentCost={1500} currentPrice={1520} />
      </div>,
      ['position_calculator'],
    )
    expect(screen.getByTestId('calc-host').textContent).not.toBe('')
  })
})

describe('onboarding', () => {
  it('shows the disclaimer on every step', async () => {
    const user = userEvent.setup()
    await renderLoaded(<Onboarding open onComplete={() => {}} hasStocks />)
    const expectDisclaimer = () =>
      expect(screen.getByTestId('onboarding-disclaimer').textContent).toBe(SERVER_SHORT)
    expectDisclaimer()
    await user.click(screen.getByRole('button', { name: /开始使用/ }))
    expectDisclaimer()
    await user.click(screen.getByRole('button', { name: '稍后再说' }))
    expectDisclaimer()
    await user.click(screen.getByRole('button', { name: '稍后再说' }))
    expect(screen.getByText('设置完成')).toBeTruthy()
    expectDisclaimer()
  })
})

describe('deep analysis sections', () => {
  const raw = {
    research_summary: '### Summary\nDemand is recovering.',
    final_decision: '**Rating**: Buy',
    trader_plan: 'Buy 100 shares',
    analyst_reports: { market: 'Trend up.', social: '', news: '', fundamentals: '' },
    debate_history: { history: 'Bull and bear views.', judge_decision: 'Go long.' },
    risk_debate: { history: 'Risk views.', judge_decision: 'Approve.' },
  } as never

  it('exclude the decision, trader plan and risk debate by default', () => {
    const sections = buildAnalysisSections(raw)
    expect(sections.map((s) => s.id)).toEqual(['summary', 'market', 'debate'])
    const text = sections.map((s) => s.markdown).join('\n')
    expect(text).not.toContain('Buy')
    expect(text).not.toContain('Go long.')
  })

  it('include them only when rating is enabled', () => {
    const ids = buildAnalysisSections(raw, { includeDecision: true }).map((s) => s.id)
    expect(ids).toEqual(['summary', 'decision', 'market', 'debate', 'risk'])
  })
})
