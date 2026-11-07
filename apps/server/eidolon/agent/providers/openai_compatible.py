"""OpenAI-compatible chat completions over httpx: DeepSeek (default), Qwen, vLLM,
Ollama and the like. Streams ``/chat/completions`` SSE with ``stream_options.include_usage``
so the final chunk carries token counts; ``/embeddings`` for ``embed``."""

import json
import time
from collections.abc import AsyncIterator, Sequence
from typing import Any

import httpx

from eidolon.agent.providers.base import (
    ChatEvent,
    ChatRequest,
    Completed,
    Message,
    ProviderError,
    ProviderTimeout,
    RetryPolicy,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    Usage,
    with_retry,
)

NAME = "openai_compatible"
RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})

_FINISH = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


def _messages(request: ChatRequest) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if request.system:
        out.append({"role": "system", "content": request.system})
    for m in request.messages:
        if m.role == "user":
            out.append({"role": "user", "content": m.content or ""})
        elif m.role == "assistant":
            entry: dict[str, Any] = {"role": "assistant", "content": m.content or ""}
            if m.provider_state and m.provider_state.get("reasoning_content") is not None:
                # DeepSeek thinking mode: the chain must come back with the turn.
                entry["reasoning_content"] = m.provider_state["reasoning_content"]
            if m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {"name": c.name, "arguments": json.dumps(dict(c.arguments))},
                    }
                    for c in m.tool_calls
                ]
            out.append(entry)
        else:
            assert m.tool_result is not None
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_result.tool_call_id,
                    "content": m.tool_result.content,
                }
            )
    return out


def _tools(request: ChatRequest) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": dict(t.input_schema),
            },
        }
        for t in request.tools
    ]


class OpenAICompatible:
    name = NAME

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        timeout: float = 120.0,
        retry: RetryPolicy | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        embedding_model: str = "text-embedding-3-small",
    ):
        self.base_url = base_url.rstrip("/")
        self.retry = retry or RetryPolicy()
        self.embedding_model = embedding_model
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # -- chat ----------------------------------------------------------------------

    def complete(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        return with_retry(self.retry, lambda: self._open(request))

    async def _open(self, request: ChatRequest) -> AsyncIterator[ChatEvent]:
        body: dict[str, Any] = {
            "model": request.model,
            "messages": _messages(request),
            "max_tokens": request.max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if request.tools:
            body["tools"] = _tools(request)
        if request.temperature is not None:
            body["temperature"] = request.temperature
        started = time.monotonic()
        req = self._client.build_request("POST", "/chat/completions", json=body)
        try:
            response = await self._client.send(req, stream=True)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"timeout: {exc}", provider=NAME) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection failed: {exc}", provider=NAME, retryable=True) from exc
        request_id = response.headers.get("x-request-id")
        if response.status_code >= 400:
            text = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            retry_after = response.headers.get("retry-after")
            raise ProviderError(
                f"HTTP {response.status_code}: {text[:500]}",
                provider=NAME,
                retryable=response.status_code in RETRYABLE_STATUS,
                status=response.status_code,
                request_id=request_id,
                retry_after=float(retry_after) if retry_after and retry_after.isdigit() else None,
            )
        return self._events(response, request, request_id, started)

    async def _events(
        self, response: httpx.Response, request: ChatRequest, request_id: str | None, started: float
    ) -> AsyncIterator[ChatEvent]:
        pending: dict[int, dict[str, Any]] = {}  # tool call index -> {id, name, args}
        order: list[int] = []
        usage: Usage | None = None
        stop = "other"
        model = request.model
        chunk_id: str | None = None
        reasoning: list[str] = []
        try:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError as exc:
                    raise ProviderError(
                        f"bad SSE chunk: {data[:200]}", provider=NAME, request_id=request_id
                    ) from exc
                chunk_id = chunk.get("id") or chunk_id
                model = chunk.get("model") or model
                if chunk.get("usage"):
                    usage = _usage(chunk["usage"])
                for choice in chunk.get("choices") or ():
                    delta = choice.get("delta") or {}
                    if delta.get("reasoning_content"):
                        reasoning.append(delta["reasoning_content"])
                    if delta.get("content"):
                        yield TextDelta(delta["content"])
                    for part in delta.get("tool_calls") or ():
                        idx = part.get("index", 0)
                        slot = pending.setdefault(idx, {"id": None, "name": "", "args": ""})
                        if idx not in order:
                            order.append(idx)
                        if part.get("id"):
                            slot["id"] = part["id"]
                        fn = part.get("function") or {}
                        if fn.get("name"):
                            slot["name"] += fn["name"]
                        if fn.get("arguments"):
                            slot["args"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        stop = _FINISH.get(choice["finish_reason"], "other")
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"timeout mid-stream: {exc}", provider=NAME) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"stream broke: {exc}", provider=NAME, retryable=False, request_id=request_id
            ) from exc
        finally:
            await response.aclose()

        for idx in order:
            slot = pending[idx]
            try:
                args = json.loads(slot["args"]) if slot["args"] else {}
            except json.JSONDecodeError as exc:
                raise ProviderError(
                    f"tool call {slot['name']!r} has invalid JSON arguments: {slot['args'][:200]}",
                    provider=NAME,
                    request_id=request_id,
                ) from exc
            yield ToolCallEvent(ToolCall(slot["id"] or f"call_{idx}", slot["name"], args))
        yield Completed(
            stop_reason=stop,
            usage=usage or Usage(),
            model=model,
            provider=NAME,
            request_id=request_id or chunk_id,
            latency_ms=round((time.monotonic() - started) * 1000),
            assistant_state={"reasoning_content": "".join(reasoning)} if reasoning else None,
        )

    # -- embeddings ----------------------------------------------------------------

    async def embed(self, texts: Sequence[str], *, model: str | None = None) -> list[list[float]]:
        body = {"model": model or self.embedding_model, "input": list(texts)}
        try:
            response = await self._client.post("/embeddings", json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(f"timeout: {exc}", provider=NAME) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"connection failed: {exc}", provider=NAME, retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"HTTP {response.status_code}: {response.text[:500]}",
                provider=NAME,
                retryable=response.status_code in RETRYABLE_STATUS,
                status=response.status_code,
            )
        data = sorted(response.json()["data"], key=lambda d: d.get("index", 0))
        return [list(map(float, d["embedding"])) for d in data]


def _usage(raw: dict[str, Any]) -> Usage:
    details = raw.get("prompt_tokens_details") or {}
    cached = raw.get("prompt_cache_hit_tokens", details.get("cached_tokens", 0)) or 0
    return Usage(
        input_tokens=int(raw.get("prompt_tokens", 0) or 0),
        output_tokens=int(raw.get("completion_tokens", 0) or 0),
        cache_read_tokens=int(cached),
    )


__all__ = ["OpenAICompatible", "NAME", "Message"]
