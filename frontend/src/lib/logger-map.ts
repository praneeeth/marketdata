// Map Python module logger names to short display names
export const LOGGER_MAPPING: Record<string, string> = {
  // Agents
  'src.modules.automation.daily_report': 'Daily close report',
  'src.modules.automation.premarket_outlook': 'Pre-market outlook',
  'src.modules.automation.intraday_monitor': 'Intraday monitor',
  'src.modules.automation.base': 'Agent runs',
  'src.modules.automation.news_digest': 'News digest',
  'src.modules.automation.agent_scheduler': 'Scheduler',
  'src.modules.automation.suggestion_pool': 'Suggestion pool',
  'src.modules.automation.tradingagents': 'Deep research',
  'src.modules.automation.tradingagents.agent': 'Deep research: main flow',
  'src.modules.automation.tradingagents.observability': 'Deep research: progress and cost',
  'src.modules.automation.tradingagents.data_context': 'Deep research: data context',
  'src.modules.automation.tradingagents.toolkit_adapter': 'Deep research: data adapter',
  'src.modules.automation.tradingagents.decision': 'Deep research: decision and simulation',
  'src.modules.automation.tradingagents.runtime_support': 'Deep research: runtime compatibility',
  'src.modules.automation.tradingagents.operations': 'Deep research: triggers and evaluation',
  'tradingagents': 'Deep research (upstream)',

  // Modules
  'src.modules.assistant': 'Assistant',
  'src.modules.market': 'Market',
  'src.modules.portfolio': 'Portfolio',
  'src.modules.research': 'Research',
  'src.modules.research.analysis_history': 'Analysis history',
  'src.modules.strategy': 'Strategy',
  'src.modules.paper_trading': 'Simulation',
  'src.modules.administration': 'Administration',

  // Platform
  'src.platform.ai': 'AI client',
  'src.platform.notifications': 'Notifications',
  'src.platform.marketdata': 'Market data',
  'src.platform.marketdata.collectors.kline_collector': 'K-line collection',
  'src.platform.marketdata.collectors.news_collector': 'News collection',
  'src.platform.persistence': 'Database',
  'src.platform.scheduling': 'Scheduling',
  'src.platform.compliance': 'Compliance',

  // Entry
  'server': 'Server',

  // Third-party & infra
  'httpx': 'HTTP client',
  'httpcore': 'HTTP core',
  'urllib3': 'HTTP library',
  'requests': 'HTTP client',
  'uvicorn.access': 'Access log',
  'uvicorn.error': 'Uvicorn errors',
  'uvicorn': 'Uvicorn',
  'fastapi': 'FastAPI',
  'starlette': 'Starlette',
  'sqlalchemy.engine': 'Database engine',
  'sqlalchemy': 'SQLAlchemy',
  'apscheduler': 'APScheduler',
  'openai': 'AI SDK',
  'tenacity': 'Retry library',
}

export function mapLoggerName(moduleName?: string): string {
  if (!moduleName) return ''
  let bestKey = ''
  for (const key of Object.keys(LOGGER_MAPPING)) {
    if (moduleName === key || moduleName.startsWith(key)) {
      if (key.length > bestKey.length) bestKey = key
    }
  }
  return LOGGER_MAPPING[bestKey] || moduleName
}

export function loggerOptions(): { key: string, label: string }[] {
  return Object.entries(LOGGER_MAPPING).map(([key, label]) => ({ key, label }))
}
