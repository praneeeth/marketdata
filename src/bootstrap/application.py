"""ASGI application composition root.

This is the only place that creates the :class:`fastapi.FastAPI` instance at startup. It only wires
HTTP middleware, auth dependencies and each module's router; business rules stay in ``modules`` and
``platform``, so the app entry point doesn't turn into a new general business layer.
"""

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from src.modules.administration.api import (
    auth,
    channels,
    compliance,
    health,
    logs,
    mcp,
    pats,
    providers,
    settings,
)
from src.modules.administration.api.auth import get_current_user
from src.modules.administration.api.settings import get_app_version
from src.modules.assistant import api as assistant_api
from src.modules.assistant import chat_api
from src.modules.assistant.task_runner import assistant_task_runner
from src.modules.automation.api import agents, suggestions, templates
from src.modules.market.api import (
    brokers,
    global_markets,
    klines,
    market,
    news,
    price_alerts,
    quotes,
    stocks,
)
from src.modules.paper_trading.api import paper_trading
from src.modules.portfolio.api import accounts, dashboard, history
from src.modules.research.api import (
    context,
    evaluations,
    feedback,
    insights,
    recommendations,
)
from src.modules.strategy.api import factors
from src.modules.market.brokers import get_broker_manager
from src.platform.compliance import Feature, is_feature_enabled
from src.platform.compliance.http import feature_gate
from src.platform.marketdata.india_bridge import register_broker_manager
from src.web.response import ResponseWrapperMiddleware

# Market "IN" quotes/K-lines (platform layer) read the user's broker sessions (modules layer).
register_broker_manager(get_broker_manager)

app = FastAPI(
    title="Candlewise API",
    version="0.1.0",
    redirect_slashes=False,  # avoid redirects that drop the Authorization header
)

app.add_middleware(ResponseWrapperMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth routes (no login needed)
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
# Global cues (world indices, crude, gold, USD/INR): public context data, no user data.
app.include_router(global_markets.router, prefix="/api/market", tags=["market"])
# Compliance status and disclaimer (status is public; /ack checks login itself)
app.include_router(compliance.router, prefix="/api/compliance", tags=["compliance"])
# Broker login callbacks: the broker redirects the browser here, protected by the login state.
app.include_router(brokers.callback_router, prefix="/api/brokers", tags=["brokers"])

# Routes that need login
protected = [Depends(get_current_user)]
# Indian indices come from the user's own broker connection, so they need login.
app.include_router(market.router, prefix="/api/market", tags=["market"], dependencies=protected)
app.include_router(
    stocks.router, prefix="/api/stocks", tags=["stocks"], dependencies=protected
)
app.include_router(
    quotes.router, prefix="/api/quotes", tags=["quotes"], dependencies=protected
)
app.include_router(
    brokers.router, prefix="/api/brokers", tags=["brokers"], dependencies=protected
)
app.include_router(
    klines.router, prefix="/api/klines", tags=["klines"], dependencies=protected
)
app.include_router(
    insights.router, prefix="/api/insights", tags=["insights"], dependencies=protected
)
app.include_router(
    accounts.router, prefix="/api", tags=["accounts"], dependencies=protected
)
app.include_router(
    agents.router, prefix="/api/agents", tags=["agents"], dependencies=protected
)
app.include_router(
    providers.router,
    prefix="/api/providers",
    tags=["providers"],
    dependencies=protected,
)
app.include_router(
    channels.router, prefix="/api/channels", tags=["channels"], dependencies=protected
)
app.include_router(
    settings.router, prefix="/api/settings", tags=["settings"], dependencies=protected
)
app.include_router(
    logs.router, prefix="/api/logs", tags=["logs"], dependencies=protected
)
app.include_router(
    history.router, prefix="/api", tags=["history"], dependencies=protected
)
app.include_router(
    context.router, prefix="/api", tags=["context"], dependencies=protected
)
app.include_router(
    evaluations.router,
    prefix="/api/evaluations",
    tags=["evaluations"],
    dependencies=[*protected, Depends(feature_gate(Feature.EVALUATIONS))],
)
app.include_router(
    news.router, prefix="/api/news", tags=["news"], dependencies=protected
)
app.include_router(
    suggestions.router,
    prefix="/api/suggestions",
    tags=["suggestions"],
    dependencies=[*protected, Depends(feature_gate(Feature.SUGGESTION_POOL))],
)
app.include_router(
    templates.router,
    prefix="/api/templates",
    tags=["templates"],
    dependencies=protected,
)
app.include_router(
    feedback.router,
    prefix="/api/feedback",
    tags=["feedback"],
    dependencies=[*protected, Depends(feature_gate(Feature.SUGGESTION_POOL))],
)

app.include_router(
    price_alerts.router,
    prefix="/api/price-alerts",
    tags=["price-alerts"],
    dependencies=protected,
)
app.include_router(
    recommendations.router,
    prefix="/api/recommendations",
    tags=["recommendations"],
    dependencies=[*protected, Depends(feature_gate(Feature.ENTRY_CANDIDATES))],
)
app.include_router(
    dashboard.router,
    prefix="/api/dashboard",
    tags=["dashboard"],
    dependencies=protected,
)
app.include_router(
    factors.router,
    prefix="/api/factors",
    tags=["factors"],
    dependencies=[*protected, Depends(feature_gate(Feature.STRATEGY_SIGNALS))],
)
app.include_router(
    health.router,
    prefix="/api/health",
    tags=["health"],
    dependencies=protected,
)
app.include_router(
    paper_trading.router,
    prefix="/api/paper-trading",
    tags=["paper-trading"],
    dependencies=protected,
)
app.include_router(
    chat_api.router,
    prefix="/api/chat",
    tags=["chat"],
    dependencies=protected,
)
app.include_router(
    assistant_api.router,
    prefix="/api/assistant",
    tags=["assistant"],
    dependencies=protected,
)


app.router.on_startup.append(assistant_task_runner.recover_pending)
MCP_ENABLED = is_feature_enabled(Feature.MCP_SERVER)

if MCP_ENABLED:
    # PAT management (login needed): create/list/revoke personal access tokens for MCP
    app.include_router(
        pats.router, prefix="/api/pats", tags=["pats"], dependencies=protected
    )
    # MCP server: mounted at top-level /mcp (not under /api/, bypassing the response wrapper so JSON-RPC is unchanged),
    # with its own PAT auth instead of the login JWT. Disabled by default in the India fork (MCP_ENABLED).
    app.include_router(mcp.router, prefix="/mcp", tags=["mcp"])


def oauth_protected_resource_metadata(request: Request, _resource_path: str = ""):
    """RFC 9728 metadata: MCP clients probe this endpoint before the handshake to choose an auth method.

    The app uses static PATs (no OAuth server), so it returns authorization_servers=[] +
    bearer_methods_supported=["header"], telling clients to use Authorization Bearer directly.
    The endpoint must exist even without OAuth, or clients get a 404 and fail on the schema mismatch.
    """
    base = str(request.base_url).rstrip("/")
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [],
        "bearer_methods_supported": ["header"],
    }


if MCP_ENABLED:
    app.get("/.well-known/oauth-protected-resource", include_in_schema=False)(
        oauth_protected_resource_metadata
    )
    app.get(
        "/.well-known/oauth-protected-resource/{_resource_path:path}",
        include_in_schema=False,
    )(oauth_protected_resource_metadata)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/version")
async def version():
    """Get the app version (public endpoint)."""
    return {"version": get_app_version()}
