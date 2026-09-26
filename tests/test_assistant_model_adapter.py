"""Model adaptation stays in the Candlewise host, outside pan_agent."""

import asyncio

from pan_agent import ModelMessage, ToolCall, ToolRisk, ToolSpec
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    ChatConversation,  # noqa: F401 - registers metadata
)


def test_failover_model_adapter_forwards_each_model_stream_chunk_and_maps_tool_calls():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    class FakeClient:
        async def chat_stream(self, messages, tools, temperature):
            assert messages[0]["role"] == "user"
            assert tools[0]["function"]["name"] == "get_portfolio"
            assert temperature == 0.5
            yield ("token", "Looked ")
            yield ("token", "up")
            yield ("message", {
                "content": "Looked up",
                "tool_calls": [{"id": "call-1", "name": "get_portfolio", "arguments": "{}"}],
                "usage": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                    "cached_input_tokens": 80,
                    "reasoning_output_tokens": 12,
                    "model": "test-model",
                    "source": "provider",
                },
            })

    emitted: list[str] = []

    async def emit(token: str) -> None:
        emitted.append(token)

    turn = asyncio.run(FailoverModelAdapter(FakeClient()).run_turn(
        [ModelMessage(role="user", content="my holdings")],
        [ToolSpec(name="get_portfolio", title="Holdings", description="Get holdings", risk=ToolRisk.READ,
                  input_schema={"type": "object", "properties": {}})],
        emit,
    ))

    assert turn.content == "Looked up"
    assert turn.tool_calls[0].name == "get_portfolio"
    assert turn.usage.input_tokens == 120
    assert turn.usage.cached_input_tokens == 80
    assert emitted == ["Looked ", "up"]


def test_failover_model_adapter_encodes_tool_call_history_for_model():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    captured_messages: list[dict] = []

    class FakeClient:
        async def chat_stream(self, messages, tools, temperature):
            captured_messages.extend(messages)
            yield ("message", {"content": "Answered from the holdings", "tool_calls": []})

    async def ignore_token(_token: str) -> None:
        return None

    turn = asyncio.run(FailoverModelAdapter(FakeClient()).run_turn(
        [
            ModelMessage(
                role="assistant",
                tool_calls=[ToolCall(id="call-1", name="get_portfolio", arguments={})],
            ),
            ModelMessage(
                role="tool",
                name="get_portfolio",
                tool_call_id="call-1",
                content="Real holdings: Tata Motors",
            ),
        ],
        [],
        ignore_token,
    ))

    assert turn.content == "Answered from the holdings"
    assert captured_messages == [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-1",
                "type": "function",
                "function": {"name": "get_portfolio", "arguments": "{}"},
            }],
        },
        {
            "role": "tool",
            "content": "Real holdings: Tata Motors",
            "tool_call_id": "call-1",
            "name": "get_portfolio",
        },
    ]


def test_failover_model_adapter_requires_a_tool_without_streaming_action_preamble():
    from src.modules.assistant.llm_adapter import FailoverModelAdapter

    class FakeClient:
        async def chat_stream(
            self, messages, tools, temperature, tool_choice=None
        ):
            assert tool_choice == "required"
            yield ("token", "I've updated it")
            yield (
                "message",
                {
                    "content": "I've updated it",
                    "tool_calls": [
                        {"id": "call-1", "name": "update_price_alert", "arguments": "{}"}
                    ],
                },
            )

    emitted: list[str] = []

    async def emit(token: str) -> None:
        emitted.append(token)

    turn = asyncio.run(
        FailoverModelAdapter(FakeClient()).run_turn(
            [ModelMessage(role="user", content="change the alert")],
            [
                ToolSpec(
                    name="update_price_alert",
                    title="Update alert",
                    description="Update a price alert",
                    risk=ToolRisk.WRITE,
                    input_schema={"type": "object", "properties": {}},
                )
            ],
            emit,
            tool_choice="required",
        )
    )

    assert turn.tool_calls[0].name == "update_price_alert"
    assert emitted == []


def test_assistant_service_builds_panagent_runtime_from_host_adapters():
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    runtime = AssistantService(AssistantRepository(session)).build_runtime(object())

    assert runtime.__class__.__name__ == "AgentRuntime"
    session.close()
