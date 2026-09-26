"""AI chat API endpoints."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.assistant.chat_planner import (
    run_portfolio_diagnosis,
    should_use_planning,
)
from src.modules.assistant.legacy_chat_tools import (
    CHAT_TOOLS as LEGACY_CHAT_TOOLS,
)
from src.modules.assistant.legacy_chat_tools import (
    TOOL_ERROR_PREFIX,
    build_portfolio_context,
    build_stock_context,
    build_watchlist_context,
    execute_chat_tool,
    fetch_realtime_context,
    fetch_technical_context,
)
from src.modules.assistant.prompt import ASSISTANT_SYSTEM_PROMPT as SYSTEM_PROMPT  # noqa: F401 (eval harness)
from src.modules.assistant.prompt import system_prompt_for
from src.modules.assistant.repository import AssistantRepository
from src.platform.compliance import ensure_guarded
from src.platform.ai.ai_failover import get_configured_failover_client
from src.platform.events.sse import SSEStream, chat_stream_hub
from src.platform.persistence.database import SessionLocal, get_db
from src.platform.persistence.models import (
    ChatConversation,
    ChatMessage,
    Position,
    Stock,
    StockSuggestion,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_HISTORY_MESSAGES = 20
MAX_TOOL_ROUNDS = 5

# ──────────────── Shared legacy tool boundary ────────────────
# The route retains private aliases so its stream code and tests keep their
# contract; reusable implementations live outside this HTTP router.
CHAT_TOOLS = LEGACY_CHAT_TOOLS
_build_watchlist_context = build_watchlist_context
_execute_tool = execute_chat_tool
_get_ai_client = get_configured_failover_client
_build_stock_context = build_stock_context
_build_portfolio_context = build_portfolio_context
_fetch_realtime_context = fetch_realtime_context
_fetch_technical_context = fetch_technical_context


class CreateConversationBody(BaseModel):
    stock_symbol: str | None = None
    stock_market: str | None = None
    initial_context: str | None = None


class SendMessageBody(BaseModel):
    content: str

@router.get("/suggested-questions")
def suggested_questions(
    symbol: str = Query(..., description="Stock symbol"),
    market: str = Query("IN", description="Market"),
    db: Session = Depends(get_db),
):
    """Suggested questions from the stock's current state (templates only; no AI call)."""
    questions: list[str] = []

    # Latest item
    latest_suggestion = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.stock_symbol == symbol,
            StockSuggestion.stock_market == market,
        )
        .order_by(StockSuggestion.created_at.desc())
        .first()
    )
    if latest_suggestion:
        action = (latest_suggestion.action or "").lower()
        label = latest_suggestion.action_label or latest_suggestion.action or ""
        if action in ("buy", "add"):
            questions.append(f"Is the latest \"{label}\" signal reliable? How is the timing?")
        elif action in ("sell", "reduce"):
            questions.append(f"The latest item says \"{label}\". What should I look at now?")
        elif action == "alert":
            questions.append("What was the recent price alert about? Does it need attention?")

    # Position (Position links to Stock through stock_id)
    has_position = (
        db.query(Position)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Stock.symbol == symbol, Stock.market == market)
        .first()
    ) is not None
    if has_position:
        questions.append("How is my current position doing? What are the risks?")
    else:
        questions.append("What should I research before looking at this stock?")

    # General questions
    questions.append("Analyse the recent trend and key support and resistance levels")
    questions.append("Is there news or an event worth watching?")

    return {"questions": questions[:5]}


@router.post("/conversations")
def create_conversation(
    body: CreateConversationBody | None = None,
    db: Session = Depends(get_db),
):
    conv = ChatConversation(
        stock_symbol=body.stock_symbol if body else None,
        stock_market=body.stock_market if body else None,
        initial_context=body.initial_context if body else None,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return {
        "id": conv.id,
        "title": conv.title or "",
        "stock_symbol": conv.stock_symbol,
        "stock_market": conv.stock_market,
        "created_at": str(conv.created_at or ""),
    }


@router.get("/conversations")
def list_conversations(
    limit: int = Query(30, ge=1, le=100),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(ChatConversation)
        .order_by(ChatConversation.updated_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "title": c.title or "",
            "stock_symbol": c.stock_symbol,
            "stock_market": c.stock_market,
            "created_at": str(c.created_at or ""),
        }
        for c in rows
    ]


@router.get("/conversations/{conversation_id}")
def get_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    return {
        "conversation": {
            "id": conv.id,
            "title": conv.title or "",
            "stock_symbol": conv.stock_symbol,
            "stock_market": conv.stock_market,
            "created_at": str(conv.created_at or ""),
        },
        "messages": [
            {
                "id": m.id,
                "role": m.role,
                "content": m.content,
                "created_at": str(m.created_at or ""),
            }
            for m in messages
        ],
    }


@router.delete("/conversations/{conversation_id}")
def delete_conversation(conversation_id: int, db: Session = Depends(get_db)):
    conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
    if not conv:
        raise HTTPException(404, "Conversation not found")
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation_id).delete()
    db.delete(conv)
    db.commit()
    return {"ok": True}


def _save_user_message(db: Session, conv: ChatConversation, content: str) -> ChatMessage:
    """Save the user message and set the conversation title if needed (shared by streaming and non-streaming)."""
    user_msg = ChatMessage(
        conversation_id=conv.id,
        role="user",
        content=content,
    )
    db.add(user_msg)

    # Update the conversation title (first 20 characters of the first message)
    if not conv.title:
        conv.title = content[:20]

    db.commit()
    db.refresh(user_msg)
    return user_msg


async def _build_messages_for_ai(db: Session, conv: ChatConversation) -> list[dict]:
    """Build the full messages for the model (system prompt + history + data context; shared by both endpoints)."""
    messages_for_ai: list[dict] = []

    # History
    history = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.asc())
        .all()
    )
    latest_user = next((m.content for m in reversed(history) if m.role == "user"), None)

    # System prompt (with a turn-level compliance notice for advice requests)
    system_content = system_prompt_for(latest_user)

    # Linked stock
    if conv.stock_symbol and conv.stock_market:
        system_content += f"\n\nThis conversation is about: {conv.stock_market}:{conv.stock_symbol}"

    # Page snapshot from the frontend (passed when the conversation was created)
    if conv.initial_context:
        system_content += "\n\n--- User's page snapshot (at conversation start) ---\n" + conv.initial_context

    messages_for_ai.append({"role": "system", "content": system_content})

    recent = history[-MAX_HISTORY_MESSAGES:] if len(history) > MAX_HISTORY_MESSAGES else history
    for m in recent:
        if m.role in ("user", "assistant"):
            messages_for_ai.append({"role": m.role, "content": m.content})

    # Base context (holdings + quote and items for the linked stock)
    context_parts: list[str] = []

    # User holdings
    portfolio_ctx = _build_portfolio_context(db)
    if portfolio_ctx:
        context_parts.append(portfolio_ctx)

    # Live data for the linked stock
    if conv.stock_symbol and conv.stock_market:
        realtime = await _fetch_realtime_context(conv.stock_symbol, conv.stock_market)
        if realtime:
            context_parts.append(realtime)
        technical = await _fetch_technical_context(conv.stock_symbol, conv.stock_market)
        if technical:
            context_parts.append(technical)
        stock_ctx = _build_stock_context(db, conv.stock_symbol, conv.stock_market)
        if stock_ctx:
            context_parts.append(stock_ctx)

    if context_parts:
        # Append the context to the system message
        messages_for_ai[0]["content"] += "\n\n--- Current data ---\n" + "\n\n".join(context_parts)

    return messages_for_ai


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: int,
    body: SendMessageBody,
):
    """Send a message and get the AI reply (non-streaming; kept for compatibility and as a fallback)."""
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "Conversation not found")

        _save_user_message(db, conv, body.content)
        messages_for_ai = await _build_messages_for_ai(db, conv)

        # Call the AI (with tool use to fetch more data on demand; fails over if the main model fails)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                try:
                    response_msg = await ai_client.chat_with_tools(
                        messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                    )
                except Exception:
                    # The model doesn't support tool use -> plain chat_multi
                    logger.info("Tool use unavailable; using plain chat")
                    ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                    break

                if not response_msg.tool_calls:
                    ai_response = response_msg.content or ""
                    break

                # Run the tool calls
                messages_for_ai.append({
                    "role": "assistant",
                    "content": response_msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in response_msg.tool_calls
                    ],
                })

                for tc in response_msg.tool_calls:
                    tool_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    logger.info(f"Tool call: {tc.function.name}({tool_args})")
                    result = await _execute_tool(db, tc.function.name, tool_args)
                    messages_for_ai.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })
            else:
                ai_response = response_msg.content or "Sorry, that took too many rounds. Please simplify the question and try again."

        except Exception as e:
            logger.error(f"AI chat failed: {e}")
            ai_response = f"Sorry, the AI service is unavailable right now: {e}"

        # Save the AI reply
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ensure_guarded(ai_response, surface="chat_message"),
        )
        db.add(assistant_msg)

        # Update the conversation time
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)

        return {
            "id": assistant_msg.id,
            "role": "assistant",
            "content": assistant_msg.content,
            "created_at": str(assistant_msg.created_at or ""),
        }
    finally:
        db.close()


# ──────────────── SSE streaming chat ────────────────
#
# Event types (each has an increasing id for Last-Event-ID resume):
# - meta:            {stream_id, conversation_id, user_message_id} first event, locates the stream on reconnect
# - token:           {text} incremental text; interim text from tool-call rounds is streamed too, and the
#                    frontend should clear its buffer on tool_call_start (only the final answer is saved)
# - tool_call_start: {name, arguments} the model decided to call a tool (frontend shows "Looking up…")
# - tool_result:     {name, ok, preview} tool finished (preview truncated; the full result goes only to the model)
# - done:            {message_id, content, created_at} final answer (saved)
# - error:           {message} AI service error (the error text is saved too, same as the non-streaming endpoint)
#
# Generation is decoupled from the SSE connection: the task pushes events into the SSEStream buffer, so a
# dropped connection doesn't stop generation or saving; the frontend resumes with GET /chat/streams/{stream_id} + Last-Event-ID.

TOOL_RESULT_PREVIEW_CHARS = 200


async def _run_chat_stream_task(
    conversation_id: int,
    stream: SSEStream,
    task_id: int | None = None,
) -> None:
    """Run the conversation in the background (tool loop + token stream), pushing events into stream."""
    db = SessionLocal()
    task_repository = AssistantRepository(db)
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            await stream.publish("error", {"message": "Conversation not found"})
            return

        messages_for_ai = await _build_messages_for_ai(db, conv)
        ai_client = _get_ai_client(db, conv.ai_model_id)
        ai_response = ""

        # P2 pilot: a "full portfolio check" intent goes through the plan-driven path (reuses the tool executor; plan events go to the frontend)
        latest_user = next(
            (m.get("content") or "" for m in reversed(messages_for_ai) if m.get("role") == "user"),
            "",
        )
        if should_use_planning(latest_user):
            try:
                ai_response = await run_portfolio_diagnosis(
                    db, stream, ai_client, _execute_tool
                )
            except Exception as e:
                logger.error(f"Plan-driven portfolio check failed: {e}")
                ai_response = f"Sorry, the portfolio check failed: {e}"
                await stream.publish("error", {"message": str(e)})
        else:
            try:
                final_msg: dict | None = None
                for _round in range(MAX_TOOL_ROUNDS):
                    final_msg = None
                    try:
                        async for kind, payload in ai_client.chat_stream(
                            messages_for_ai, tools=CHAT_TOOLS, temperature=0.5,
                        ):
                            if kind == "token":
                                await stream.publish("token", {"text": payload})
                            else:
                                final_msg = payload
                    except Exception:
                        # The model doesn't support tool use / streaming -> plain chat (same as the non-streaming endpoint)
                        logger.info("Streaming tool use unavailable; falling back to plain chat")
                        ai_response = await ai_client.chat_multi(messages_for_ai, temperature=0.5)
                        await stream.publish("token", {"text": ai_response})
                        break

                    tool_calls = (final_msg or {}).get("tool_calls") or []
                    if not tool_calls:
                        ai_response = (final_msg or {}).get("content") or ""
                        break

                    # Tool calls: append the assistant message and tool results to the context, then go again
                    messages_for_ai.append({
                        "role": "assistant",
                        "content": (final_msg or {}).get("content") or None,
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": tc["arguments"]},
                            }
                            for tc in tool_calls
                        ],
                    })
                    for tc in tool_calls:
                        try:
                            tool_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                        except json.JSONDecodeError:
                            tool_args = {}
                        logger.info(f"Tool call(stream): {tc['name']}({tool_args})")
                        await stream.publish(
                            "tool_call_start", {"name": tc["name"], "arguments": tool_args}
                        )
                        result = await _execute_tool(db, tc["name"], tool_args)
                        await stream.publish(
                            "tool_result",
                            {
                                "name": tc["name"],
                                "ok": not result.startswith(TOOL_ERROR_PREFIX),
                                "preview": (result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                            },
                        )
                        if task_id is not None:
                            task_repository.record_tool_completed(
                                task_id,
                                call_id=tc["id"],
                                tool_name=tc["name"],
                                summary=(result or "")[:TOOL_RESULT_PREVIEW_CHARS],
                            )
                        messages_for_ai.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": result,
                        })
                else:
                    ai_response = (final_msg or {}).get("content") or "Sorry, that took too many rounds. Please simplify the question and try again."

            except Exception as e:
                logger.error(f"AI streaming chat failed: {e}")
                ai_response = f"Sorry, the AI service is unavailable right now: {e}"
                await stream.publish("error", {"message": str(e)})

        # Save (whether or not the connection is still open)
        assistant_msg = ChatMessage(
            conversation_id=conversation_id,
            role="assistant",
            content=ensure_guarded(ai_response, surface="chat_message"),
        )
        db.add(assistant_msg)
        conv.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(assistant_msg)
        if task_id is not None:
            task_repository.finish_task(
                task_id,
                status="completed",
                final_message_id=assistant_msg.id,
            )

        await stream.publish("done", {
            "message_id": assistant_msg.id,
            "content": ai_response,
            "created_at": str(assistant_msg.created_at or ""),
            # The model label actually used (may not be the main model after failover), shown in the frontend
            "model_label": getattr(ai_client, "used_model_label", ""),
        })
    except Exception as e:
        logger.error(f"Streaming chat task failed: {e}")
        try:
            await stream.publish("error", {"message": str(e)})
        except Exception:
            pass
        if task_id is not None:
            try:
                task_repository.finish_task(
                    task_id,
                    status="failed",
                    final_message_id=None,
                    error_code="chat_stream_failed",
                )
            except Exception:
                pass
    finally:
        await stream.finish()
        db.close()


def _sse_response(stream: SSEStream, after_seq: int = 0) -> StreamingResponse:
    """Wrap an SSEStream as a text/event-stream response (the response-wrapper middleware passes this type through)."""
    return StreamingResponse(
        stream.subscribe(after_seq=after_seq),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Disable buffering in reverse proxies such as nginx so events arrive immediately
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/conversations/{conversation_id}/messages/stream")
async def send_message_stream(
    conversation_id: int,
    body: SendMessageBody,
):
    """Send a message and stream the AI reply over SSE (token stream + visible tool steps).

    The non-streaming POST /messages endpoint is kept; the frontend falls back to it if streaming fails.
    """
    db = SessionLocal()
    try:
        conv = db.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()
        if not conv:
            raise HTTPException(404, "Conversation not found")
        user_msg = _save_user_message(db, conv, body.content)
        user_message_id = user_msg.id
        task = AssistantRepository(db).create_task(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            context={
                "stock_symbol": conv.stock_symbol,
                "stock_market": conv.stock_market,
                "initial_context": conv.initial_context or "",
            },
        )
        task_id = task.id
    finally:
        db.close()

    stream = chat_stream_hub.create()
    # meta goes first: it gives the stream_id, so a dropped client can resume with GET /chat/streams/{stream_id}
    await stream.publish("meta", {
        "stream_id": stream.stream_id,
        "conversation_id": conversation_id,
        "user_message_id": user_message_id,
        "task_id": task_id,
    })
    # The generation task runs on its own and isn't cancelled when this response connection drops
    asyncio.create_task(_run_chat_stream_task(conversation_id, stream, task_id))
    return _sse_response(stream)


@router.get("/streams/{stream_id}")
async def resume_message_stream(
    stream_id: str,
    request: Request,
    last_event_id: int = Query(0, ge=0, description="Sequence number of the last event received before the disconnect"),
):
    """Reconnect: resume from the buffer by Last-Event-ID (header first, query as fallback)."""
    stream = chat_stream_hub.get(stream_id)
    if not stream:
        raise HTTPException(404, "Stream not found or expired")
    header_id = request.headers.get("last-event-id", "")
    after_seq = int(header_id) if header_id.isdigit() else last_event_id
    return _sse_response(stream, after_seq=after_seq)
