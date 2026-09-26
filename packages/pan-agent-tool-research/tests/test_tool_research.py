import asyncio

import pytest
from pan_agent import (
    AgentRuntime,
    BeforeModelTurnContext,
    DuplicateToolName,
    ExtensionToolContext,
    ModelTurn,
    ReadOnlyToolPolicy,
    RunLimits,
    RunRequest,
    ToolCall,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    UnknownTool,
)
from pan_agent_tool_research import (
    ToolDescriptor,
    ToolResearchPlugin,
    ToolResearchRequest,
    ToolResearchService,
)


async def fake_executor(_request, _arguments):
    raise AssertionError("tool research must not execute tools")


def request(content: str = "find research ideas") -> RunRequest:
    return RunRequest(
        run_id="research-run",
        messages=[{"role": "user", "content": content}],
    )


def descriptor(
    name: str,
    *,
    title: str,
    summary: str,
    keywords: list[str],
    risk: ToolRisk = ToolRisk.READ,
    domain: str = "investment_research",
) -> ToolDescriptor:
    return ToolDescriptor(
        tool_name=name,
        title=title,
        summary=summary,
        keywords=keywords,
        aliases=keywords,
        domain=domain,
        capabilities=keywords,
        risk=risk,
        confirmation_required=risk is not ToolRisk.READ,
    )


def build_registry(executor=fake_executor) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="find_research_candidates",
            title="Find research candidates",
            description="Screen stocks worth researching further.",
            input_schema={"type": "object", "properties": {}},
        ),
        executor,
    )
    registry.register(
        ToolSpec(
            name="create_price_alert",
            title="Create price alert",
            description="Create a price alert.",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={"type": "object", "properties": {}},
        ),
        executor,
    )
    return registry


def descriptors() -> list[ToolDescriptor]:
    return [
        descriptor(
            "find_research_candidates",
            title="Find research candidates",
            summary="Screen the market and holdings for stocks worth researching further.",
            keywords=["ideas", "research", "candidates", "screen"],
        ),
        descriptor(
            "create_price_alert",
            title="Create price alert",
            summary="Create a price-triggered alert for a stock.",
            keywords=["alert", "price", "create"],
            risk=ToolRisk.WRITE,
        ),
    ]


def build_service() -> ToolResearchService:
    return ToolResearchService(build_registry(), descriptors=descriptors())


def test_tool_research_selects_relevant_tool_with_explainable_reason():
    service = build_service()

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="help me find a few new research ideas"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request(),
        )
    )

    assert result.fallback is False
    assert result.selected_tools == ["find_research_candidates"]
    assert result.candidates[0].tool_name == "find_research_candidates"
    assert result.candidates[0].score > 0
    assert result.candidates[0].match_reasons
    assert result.candidates[0].domain == "investment_research"


def test_tool_research_hard_filters_write_tools_and_denied_tools():
    service = build_service()

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="create a price alert"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request("create a price alert"),
        )
    )

    assert "create_price_alert" not in result.selected_tools
    assert result.selected_tools == []


def test_tool_research_returns_empty_result_for_no_match_without_inventing_tools():
    service = build_service()

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="write an email"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request("write an email"),
        )
    )

    assert result.fallback is False
    assert result.selected_tools == []
    assert result.candidates == []


def test_registry_rejects_unknown_or_duplicate_descriptors():
    registry = ToolRegistry()
    with pytest.raises(UnknownTool):
        ToolResearchService(
            registry,
            descriptors=[
                descriptor(
                    "unknown",
                    title="Unknown",
                    summary="Unknown tool",
                    keywords=["unknown"],
                )
            ],
        )

    registry.register(
        ToolSpec(
            name="lookup",
            title="Lookup",
            description="Look up a value.",
            input_schema={"type": "object", "properties": {}},
        ),
        fake_executor,
    )
    item = descriptor(
        "lookup", title="Lookup", summary="Look up a value.", keywords=["lookup"]
    )
    with pytest.raises(DuplicateToolName):
        ToolResearchService(registry, descriptors=[item, item])


def test_plugin_emits_generic_extension_events_and_can_select_in_active_mode():
    events = []
    service = build_service()
    context = BeforeModelTurnContext(
        request=request(),
        messages=tuple(request().messages),
        available_tools=tuple(build_registry().registered_tools()),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda name, data: _record_event(events, name, data),
    )

    shadow = ToolResearchPlugin(service, mode="shadow")
    assert asyncio.run(shadow.before_model_turn(context)) is None
    assert [name for name, _data in events] == [
        "started",
        "candidates_scored",
        "completed",
    ]

    active = ToolResearchPlugin(service, mode="active")
    decision = asyncio.run(active.before_model_turn(context))
    assert decision is not None
    assert list(decision.tool_names or []) == [
        "find_research_candidates",
        "create_price_alert",
    ]
    assert [tool.name for tool in decision.additional_tools] == ["tool_search"]


def test_active_plugin_search_returns_loaded_tool_references_without_executing_tools():
    service = build_service()
    plugin = ToolResearchPlugin(service, mode="active")
    request_value = request()
    search_spec = plugin._search_tool_spec()
    context = ExtensionToolContext(
        request=request_value,
        messages=tuple(request_value.messages),
        call=ToolCall(
            id="search-1",
            name="tool_search",
            arguments={"query": "find research ideas"},
        ),
        tool=search_spec,
        available_tools=(search_spec,),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda _name, _data: _record_event([], _name, _data),
    )

    result = asyncio.run(plugin.handle_tool_call(context))

    assert result is not None
    assert result.ok is True
    assert result.data["loaded_tools"] == ["find_research_candidates"]


def test_active_plugin_search_failure_falls_back_without_emptying_direct_tools():
    class BrokenService:
        async def research(self, *_args, **_kwargs):
            raise RuntimeError("catalog unavailable")

    plugin = ToolResearchPlugin(BrokenService(), mode="active")
    request_value = request()
    search_spec = plugin._search_tool_spec()
    context = ExtensionToolContext(
        request=request_value,
        messages=tuple(request_value.messages),
        call=ToolCall(
            id="search-1",
            name="tool_search",
            arguments={"query": "lookup tool"},
        ),
        tool=search_spec,
        available_tools=(search_spec,),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda _name, _data: _record_event([], _name, _data),
    )

    result = asyncio.run(plugin.handle_tool_call(context))

    assert result is not None
    assert result.ok is True
    assert result.data["loaded_tools"] == []
    assert result.data["fallback"] is True


def test_active_plugin_completes_model_side_search_and_deferred_tool_loading():
    async def candidate_executor(_request, _arguments):
        return ToolResult.success(
            summary="Candidate lookup done",
            data={"items": []},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    registry = build_registry(executor=candidate_executor)
    registry.set_exposure("find_research_candidates", ToolExposure.DEFERRED)
    service = ToolResearchService(registry, descriptors=descriptors())
    plugin = ToolResearchPlugin(service, mode="active")

    class Model:
        def __init__(self):
            self.tool_names = []
            self.turn = 0

        async def run_turn(self, _messages, tools, _emit_token, tool_choice=None):
            self.tool_names.append([tool.name for tool in tools])
            self.turn += 1
            if self.turn == 1:
                return ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="search-1",
                            name="tool_search",
                            arguments={"query": "find research ideas"},
                        )
                    ]
                )
            if self.turn == 2:
                return ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="candidate-1",
                            name="find_research_candidates",
                            arguments={},
                        )
                    ]
                )
            return ModelTurn(content="Done")

    model = Model()

    result = asyncio.run(
        AgentRuntime(
            model,
            registry,
            policy=ReadOnlyToolPolicy(),
            extensions=[plugin],
        ).run(
            RunRequest(
                run_id="tool-search-run",
                messages=[{"role": "user", "content": "find a few research ideas"}],
                limits=RunLimits(max_steps=4),
            ),
            _CollectingSink(),
        )
    )

    assert result.status.value == "completed"
    assert model.tool_names[0] == ["tool_search"]
    assert "find_research_candidates" in model.tool_names[1]


class _CollectingSink:
    async def publish(self, _event):
        return None


async def _record_event(events, name, data):
    events.append((name, data))
