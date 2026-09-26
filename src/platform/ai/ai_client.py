import base64
import logging
from pathlib import Path

from openai import AsyncOpenAI
from pan_agent_token_meter import normalize_provider_usage

from src.platform.observability import otel

logger = logging.getLogger(__name__)


class AIClient:
    """AI client compatible with the OpenAI protocol."""

    def __init__(self, base_url: str, api_key: str, model: str = "", proxy: str = ""):
        kwargs = {
            "base_url": base_url,
            "api_key": api_key,
        }
        if proxy:
            kwargs["http_client"] = None  # TODO: configure httpx if a proxy is needed
        self.client = AsyncOpenAI(**kwargs)
        # Keep the original config as instance attributes for agents that bridge to third-party LLM frameworks
        # (e.g. TradingAgents rebuilds langchain's LLM from base_url+api_key)
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.total_tokens_used = 0
        self.last_usage = None

    async def chat(
        self,
        system_prompt: str,
        user_content: str,
        images: list[str] | None = None,
        temperature: float | None = 0.4,
    ) -> str:
        """
        Call the LLM for a text reply.

        Args:
            system_prompt: system prompt
            user_content: user input
            images: image paths (for multimodal; optional)
            temperature: sampling temperature
        """
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Build the user message
        if images:
            content_parts = [{"type": "text", "text": user_content}]
            for img_path in images:
                img_data = self._encode_image(img_path)
                if img_data:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img_data}"}
                    })
            messages.append({"role": "user", "content": content_parts})
        else:
            messages.append({"role": "user", "content": user_content})

        try:
            create_kwargs = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            # OTel gen_ai span (no-op when disabled); token usage is filled in once usage arrives.
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                # Record token usage
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )

            return response.choices[0].message.content or ""

        except Exception as e:
            logger.error(f"AI call failed: {e}")
            raise

    async def chat_multi(
        self,
        messages: list[dict],
        temperature: float | None = 0.4,
        max_tokens: int | None = None,
    ) -> str:
        """
        Multi-turn chat: pass the full messages list.

        Args:
            messages: [{"role": "system"/"user"/"assistant", "content": "..."}]
            temperature: sampling temperature; None omits the parameter
                (used by failover to retry without it on "incompatible parameter" errors)
        """
        try:
            create_kwargs: dict = {"model": self.model, "messages": messages}
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            if max_tokens is not None:
                create_kwargs["max_tokens"] = max_tokens
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
                    logger.debug(
                        f"Token usage: {response.usage.prompt_tokens} + "
                        f"{response.usage.completion_tokens} = {response.usage.total_tokens}"
                    )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error(f"AI multi-turn call failed: {e}")
            raise

    async def chat_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        temperature: float | None = 0.4,
    ):
        """Chat call with tool use; returns the raw message object.

        temperature=None omits the parameter (for failover's retry without it).
        """
        try:
            create_kwargs: dict = {
                "model": self.model,
                "messages": messages,
                "tools": tools,
            }
            if temperature is not None:
                create_kwargs["temperature"] = temperature
            with otel.llm_span(self.model, operation="chat") as _span:
                response = await self.client.chat.completions.create(**create_kwargs)
                if response.usage:
                    self.last_usage = normalize_provider_usage(response.usage, model=self.model)
                    self.total_tokens_used += response.usage.total_tokens
                    _span.set_response(
                        model=getattr(response, "model", None) or self.model,
                        input_tokens=response.usage.prompt_tokens,
                        output_tokens=response.usage.completion_tokens,
                    )
            return response.choices[0].message
        except Exception as e:
            logger.error(f"AI tool-use call failed: {e}")
            raise

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = 0.4,
        tool_choice: str | None = None,
    ):
        """Streaming chat channel (stream=True), with optional tool use.

        An async generator yielding two kinds of event:
        - ("token", str): an incremental text fragment, yielded as it is generated;
        - ("message", dict): the complete message once, after the stream ends,
          shaped {"content": full text, "tool_calls": [{"id", "name", "arguments"}, ...]};
          tool_calls is an empty list when there are no tool calls.

        The caller (e.g. the chat SSE endpoint) continues the tool loop or stops depending on whether tool_calls is empty.
        """
        create_kwargs: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        if temperature is not None:
            create_kwargs["temperature"] = temperature
        if tools:
            create_kwargs["tools"] = tools
        if tool_choice is not None:
            create_kwargs["tool_choice"] = tool_choice
        # OpenAI-compatible providers that support streaming usage return a
        # final usage-only chunk. Providers that reject this optional field
        # are retried without it below.
        create_kwargs["stream_options"] = {"include_usage": True}

        try:
            stream = await self.client.chat.completions.create(**create_kwargs)
        except Exception as e:
            message = str(e).lower()
            unsupported_stream_options = any(
                marker in message
                for marker in ("stream_options", "unsupported parameter", "unknown parameter")
            )
            if "stream_options" in create_kwargs and unsupported_stream_options:
                create_kwargs.pop("stream_options")
                try:
                    stream = await self.client.chat.completions.create(**create_kwargs)
                except Exception:
                    logger.error(f"AI streaming call failed: {e}")
                    raise
            else:
                logger.error(f"AI streaming call failed: {e}")
                raise

        content_parts: list[str] = []
        # In the OpenAI streaming protocol, tool_calls arrive in pieces by index (arguments are concatenated)
        tool_calls_acc: dict[int, dict] = {}
        provider_usage = None

        async for chunk in stream:
            # Some compatible services send a final chunk with only usage
            usage = getattr(chunk, "usage", None)
            if usage:
                self.total_tokens_used += usage.total_tokens
                provider_usage = normalize_provider_usage(usage, model=self.model)
                self.last_usage = provider_usage
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta is None:
                continue
            if delta.content:
                content_parts.append(delta.content)
                yield ("token", delta.content)
            for tc in delta.tool_calls or []:
                acc = tool_calls_acc.setdefault(
                    tc.index, {"id": "", "name": "", "arguments": ""}
                )
                if tc.id:
                    acc["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        acc["name"] = tc.function.name
                    if tc.function.arguments:
                        acc["arguments"] += tc.function.arguments

        yield (
            "message",
            {
                "content": "".join(content_parts),
                "tool_calls": [tool_calls_acc[i] for i in sorted(tool_calls_acc)],
                "usage": provider_usage.model_dump(mode="json") if provider_usage else None,
            },
        )

    async def list_models(self) -> list[str]:
        """List available model ids through the OpenAI-compatible /v1/models."""
        resp = await self.client.models.list()
        return sorted(m.id for m in resp.data)

    def _encode_image(self, image_path: str) -> str | None:
        """Encode an image file as base64."""
        path = Path(image_path)
        if not path.exists():
            logger.warning(f"Image not found: {image_path}")
            return None
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
