import asyncio

from pan_agent import ContextCompressionMode, ModelMessage


class _FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def chat_multi(self, messages, temperature=0.4):
        self.calls.append((messages, temperature))
        return self.result


def test_failover_context_summarizer_returns_structured_summary():
    from src.modules.assistant.context_summarizer import FailoverContextSummarizer

    client = _FakeClient(
        '{"goal":["analyse holdings"],"constraints":["don\'t change data"],'
        '"decisions":["keep the cash position"],"facts":["cash 50%"],'
        '"current_state":"waiting for the next step","open_items":["add a risk note"],'
        '"tool_findings":["holdings read"]}'
    )
    summary = asyncio.run(
        FailoverContextSummarizer(client, temperature=0.2).summarize(
            [ModelMessage(role="user", content="analyse holdings")],
            mode=ContextCompressionMode.BALANCED,
        )
    )

    assert summary.goal == ["analyse holdings"]
    assert client.calls[0][1] == 0.2
    assert "JSON" in client.calls[0][0][0]["content"]


def test_failover_context_summarizer_accepts_fenced_json():
    from src.modules.assistant.context_summarizer import FailoverContextSummarizer

    client = _FakeClient('```json\n{"goal":["goal"]}\n```')
    summary = asyncio.run(
        FailoverContextSummarizer(client).summarize(
            [ModelMessage(role="user", content="goal")],
            mode=ContextCompressionMode.HANDOFF,
        )
    )

    assert summary.goal == ["goal"]
