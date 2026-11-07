"""Anthropic Messages API through the official SDK.

Thinking is left to the model's default (adaptive on Claude 5.x). Retries and
timeouts are the SDK's (``max_retries`` / ``timeout``), so this provider reports
``attempts=1`` and lets SDK errors map to ``ProviderError``. With
``fallbacks=True`` the request goes through the beta endpoint with
``fallbacks: "default"`` so a safety refusal is re-run on a fallback model
inside the same call (the ``Completed`` then names the model that answered).
"""

import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import anthropic as sdk

from eidolon.agent.providers.base import (
    ChatEvent,
    ChatRequest,
    Completed,
    ProviderError,
    ProviderTimeout,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
)

NAME = "anthropic"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP = {
    "end_turn": "end_turn",
    "stop_sequence": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "refusal": "refusal",
    "pause_turn": "other",
}


def _messages(request: ChatRequest) -> list[dict[str, Any]]:
    """Consecutive tool results fold into one ``user`` turn, as the API requires
    for parallel tool calls."""
    out: list[dict[str, Any]] = []
    for m in request.messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            blocks: list[dict[str, Any]] = []
            if m.provider_state:
                # Signed thinking blocks go back unchanged, ahead of the visible content.
                blocks.extend(m.provider_state.get("thinking_blocks") or ())
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for c in m.tool_calls:
                blocks.append(
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": dict(c.arguments)}
                )
            out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
        else:
            assert m.tool_result is not None
            block = {
                "type": "tool_result",
                "tool_use_id": m.tool_result.tool_call_id,
                "content": m.tool_result.content,
            }
            if m.tool_result.is_error:
                block["is_error"] = True
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)
            else:
                out.append({"role": "user", "content": [block]})
    return out


def _tools(request: ChatRequest) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "input_schema": dict(t.input_schema)}
        for t in request.tools
    ]


class Anthropic:
    name = NAME

    def __init__(
        self,
        *,
        api_key: str | None,
        base_url: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        fallbacks: bool = False,
        http_client: Any = None,
    ):
        kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["base_url"] = base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = sdk.AsyncAnthropic(**kwargs)
        self.fallbacks = fallbacks

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        params: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_tokens,
            "messages": _messages(request),
        }
        if request.system:
            params["system"] = request.system
        if request.tools:
            params["tools"] = _tools(request)
        if request.temperature is not None:
            params["temperature"] = request.temperature
        if self.fallbacks:
            params["fallbacks"] = "default"
            params["betas"] = [FALLBACK_BETA]
            manager = self._client.beta.messages.stream(**params)
        else:
            manager = self._client.messages.stream(**params)

        started = time.monotonic()
        try:
            async with manager as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield TextDelta(event.delta.text)
                final = await stream.get_final_message()
                request_id = stream.request_id
        except sdk.APITimeoutError as exc:
            raise ProviderTimeout(f"timeout: {exc}", provider=NAME) from exc
        except sdk.APIConnectionError as exc:
            raise ProviderError(f"connection failed: {exc}", provider=NAME, retryable=True) from exc
        except sdk.APIStatusError as exc:
            retry_after = exc.response.headers.get("retry-after")
            raise ProviderError(
                f"HTTP {exc.status_code}: {exc.message}",
                provider=NAME,
                retryable=exc.status_code in {408, 409, 429, 529} or exc.status_code >= 500,
                status=exc.status_code,
                request_id=exc.response.headers.get("request-id"),
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            ) from exc

        thinking_blocks: list[dict[str, Any]] = []
        for block in final.content:
            if block.type == "tool_use":
                yield ToolCallEvent(ToolCall(block.id, block.name, dict(block.input or {})))
            elif block.type in {"thinking", "redacted_thinking"}:
                thinking_blocks.append(block.model_dump(mode="json", exclude_none=True))
        usage = final.usage
        detail = None
        if final.stop_reason == "refusal":
            stop_details = getattr(final, "stop_details", None)
            detail = getattr(stop_details, "category", None) or "refusal"
        yield Completed(
            stop_reason=_STOP.get(final.stop_reason or "", "other"),
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
            model=final.model,
            provider=NAME,
            request_id=request_id,
            latency_ms=round((time.monotonic() - started) * 1000),
            stop_detail=detail,
            assistant_state={"thinking_blocks": thinking_blocks} if thinking_blocks else None,
        )

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        raise ProviderError("anthropic has no embeddings endpoint", provider=NAME)
