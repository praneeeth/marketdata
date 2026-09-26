"""MCP server: exposes chat's 5 read-only tools as a Model Context Protocol endpoint.

Design choices:
- **Hand-written lightweight JSON-RPC** (the JSON response mode of Streamable HTTP), no mcp SDK:
  minimal dependencies, self-contained tests, a small protocol surface (read-only needs only initialize/tools/list/tools/call);
- mounted at **top-level `/mcp`** (not under `/api/`), bypassing ResponseWrapperMiddleware's
  `{code,data,message}` wrapper so JSON-RPC messages come back unchanged;
- auth uses a **separate PAT system** (pwmcp_ prefix + sha256 at rest + constant-time compare + mcp:read
  scope), separate from the login JWT; every tool is read-only; each call is audit-logged.

Tools reuse the assistant's public tool schemas and dispatcher instead of reimplementing logic.
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.orm import Session

from src.modules.administration.pat import (
    SCOPE_MCP_READ,
    hash_token,
    looks_like_pat,
    verify_pat_hash,
)
from src.modules.assistant.legacy_chat_tools import CHAT_TOOLS, TOOL_ERROR_PREFIX, execute_chat_tool
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import MCPCallLog, PersonalAccessToken

logger = logging.getLogger(__name__)
router = APIRouter()

# Protocol version (default when the client doesn't negotiate)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "Candlewise", "version": "0.1.0"}

# Read-only tool allowlist (reuses chat's tool definitions; new tools are included automatically)
READ_TOOL_NAMES = {t["function"]["name"] for t in CHAT_TOOLS}

# Throttle window (seconds) for writing last_used, so not every tool call writes to the DB
_LAST_USED_THROTTLE_S = 60
# Maximum audit summary length
_ARG_SUMMARY_MAX = 200
_ARG_VALUE_MAX = 40


# ──────────────── PAT auth ────────────────


def _to_utc(dt: datetime | None) -> datetime | None:
    """SQLite stores naive DateTimes; treat them all as UTC to avoid aware/naive comparison errors."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _bump_last_used(db: Session, row: PersonalAccessToken, request: Request) -> None:
    now = datetime.now(timezone.utc)
    last = _to_utc(row.last_used_at)
    if last is None or (now - last).total_seconds() > _LAST_USED_THROTTLE_S:
        row.last_used_at = now.replace(tzinfo=None)
        row.last_used_ip = request.client.host if request.client else None
        db.commit()


def authenticate_pat(request: Request, db: Session) -> dict:
    """Validate Authorization: Bearer pwmcp_... and return the PAT metadata; raise HTTPException on failure."""
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "Missing Bearer PAT")
    token = header[7:].strip()
    if not looks_like_pat(token):
        raise HTTPException(403, "The MCP endpoint needs a PAT (pwmcp_ prefix)")

    row = (
        db.query(PersonalAccessToken)
        .filter(PersonalAccessToken.token_hash == hash_token(token))
        .first()
    )
    if row is None or not verify_pat_hash(token, row.token_hash):
        raise HTTPException(401, "Invalid token")
    if row.revoked_at is not None:
        raise HTTPException(401, "Token revoked")
    exp = _to_utc(row.expires_at)
    if exp is not None and exp < datetime.now(timezone.utc):
        raise HTTPException(401, "Token expired")

    try:
        scopes = set(json.loads(row.scopes_json or "[]"))
    except Exception:
        scopes = set()
    if SCOPE_MCP_READ not in scopes:
        raise HTTPException(403, f"PAT is missing the required scope: {SCOPE_MCP_READ}")

    _bump_last_used(db, row, request)
    return {
        "id": row.id,
        "prefix": row.prefix,
        "name": row.name,
        "client_ip": request.client.host if request.client else None,
    }


# ──────────────── Audit log ────────────────


def _summarize_args(args: dict) -> str | None:
    """Redacted summary: structured k=v fields, truncated, for audit debugging."""
    if not args:
        return None
    parts: list[str] = []
    for k, v in args.items():
        if v is None:
            continue
        if isinstance(v, str):
            shown = v if len(v) <= _ARG_VALUE_MAX else v[: _ARG_VALUE_MAX - 1] + "…"
        elif isinstance(v, (list, tuple)):
            shown = f"[{len(v)}]"
        elif isinstance(v, dict):
            shown = f"{{{len(v)}}}"
        else:
            shown = repr(v)
        parts.append(f"{k}={shown}")
    summary = ", ".join(parts)
    return summary[:_ARG_SUMMARY_MAX] if summary else None


def _write_call_log(
    pat: dict,
    tool_name: str,
    status: str,
    error: str | None,
    args_summary: str | None,
    duration_ms: int,
    client_ip: str | None,
) -> None:
    """Write the audit log (own session; failures are silent and don't affect the main flow)."""
    db = SessionLocal()
    try:
        db.add(
            MCPCallLog(
                pat_id=pat.get("id"),
                pat_prefix=pat.get("prefix"),
                tool_name=(tool_name or "")[:200],
                status=status,
                error_message=(str(error)[:500] if error else None),
                args_summary=args_summary,
                duration_ms=duration_ms,
                client_ip=client_ip,
            )
        )
        db.commit()
    except Exception:
        logger.warning("Failed to write MCPCallLog", exc_info=True)
        db.rollback()
    finally:
        db.close()


MCP_LOG_RETENTION_DAYS = 30


def prune_mcp_logs(retention_days: int = MCP_LOG_RETENTION_DAYS) -> int:
    """Delete MCP call logs past the retention period and return the count (called by the daily schedule)."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    db = SessionLocal()
    try:
        deleted = (
            db.query(MCPCallLog).filter(MCPCallLog.called_at < cutoff).delete()
        )
        db.commit()
        if deleted:
            logger.info("MCP call log retention cleanup: deleted %d rows", deleted)
        return deleted
    except Exception:
        logger.warning("MCP call log cleanup failed", exc_info=True)
        db.rollback()
        return 0
    finally:
        db.close()


# ──────────────── JSON-RPC handling ────────────────


def _mcp_tools() -> list[dict]:
    """CHAT_TOOLS (OpenAI function schema) -> MCP tool list."""
    tools = []
    for t in CHAT_TOOLS:
        fn = t["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn.get("description", ""),
                "inputSchema": fn.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return tools


def _rpc_result(req_id, result) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def _rpc_error(req_id, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}
    )


async def _handle_tools_call(params: dict, db: Session, pat: dict, req_id) -> JSONResponse:
    import time

    name = params.get("name")
    args = params.get("arguments") or {}
    if not name:
        return _rpc_error(req_id, -32602, "Missing tool name")
    if name not in READ_TOOL_NAMES:
        _write_call_log(pat, str(name), "error", "unknown tool", None, 0, pat.get("client_ip"))
        return _rpc_error(req_id, -32602, f"Unknown or disallowed tool: {name}")

    start = time.perf_counter()
    err: str | None = None
    try:
        text = await execute_chat_tool(db, name, args if isinstance(args, dict) else {})
        is_error = text.startswith(TOOL_ERROR_PREFIX)
        if is_error:
            err = text
        return _rpc_result(
            req_id,
            {"content": [{"type": "text", "text": text}], "isError": is_error},
        )
    except Exception as e:  # noqa: BLE001 — catch-all so exceptions never cross the protocol layer
        err = str(e)
        return _rpc_error(req_id, -32603, f"Tool execution error: {e}")
    finally:
        duration_ms = int((time.perf_counter() - start) * 1000)
        _write_call_log(
            pat,
            str(name),
            "error" if err else "ok",
            err,
            _summarize_args(args if isinstance(args, dict) else {}),
            duration_ms,
            pat.get("client_ip"),
        )


@router.post("")
@router.post("/")
async def mcp_endpoint(request: Request, db: Session = Depends(get_db)):
    """MCP Streamable HTTP single endpoint: handles initialize / tools/list / tools/call, etc."""
    pat = authenticate_pat(request, db)

    try:
        payload = await request.json()
    except Exception:
        return _rpc_error(None, -32700, "JSON parse error")

    if not isinstance(payload, dict):
        return _rpc_error(None, -32600, "Only single JSON-RPC requests are supported")

    method = payload.get("method")
    req_id = payload.get("id")
    params = payload.get("params") or {}

    # Notifications (no id) need no response; return 202
    if req_id is None and isinstance(method, str) and method.startswith("notifications/"):
        return Response(status_code=202)

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return _rpc_result(
            req_id,
            {
                "protocolVersion": proto,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method == "ping":
        return _rpc_result(req_id, {})
    if method == "tools/list":
        return _rpc_result(req_id, {"tools": _mcp_tools()})
    if method == "tools/call":
        return await _handle_tools_call(params, db, pat, req_id)

    return _rpc_error(req_id, -32601, f"Method not supported: {method}")
