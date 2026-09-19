"""Provider probing (v0.2): connectivity test + model listing against provider APIs.

Used by ProviderService and by the runtime provider configurators. Network
failures degrade to ``ok=False`` / empty model lists — never raise.
"""

import asyncio
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.core.redaction import redact
from app.models.enums import ProviderType
from app.models.provider import Provider
from app.providers.base import effective_base_url


class ProbePricing(BaseModel):
    """Operator-supplied, model-specific quote; never invent a price for a model."""

    input_usd_per_million: Decimal = Field(ge=0, le=10000)
    output_usd_per_million: Decimal = Field(ge=0, le=10000)
    valid_until: datetime
    source: str = Field(min_length=1, max_length=500)


def model_probe_quote(provider: Provider, model: str, limits) -> dict:
    """Validate a quote and reject an unaffordable request before network access."""
    raw = ((provider.metadata_json or {}).get("probe_pricing") or {}).get(model)
    try:
        quote = ProbePricing.model_validate(raw)
        now = datetime.now(UTC)
        if quote.valid_until.tzinfo is None or not now < quote.valid_until <= now + timedelta(
            days=7
        ):
            raise ValueError
    except (ValidationError, ValueError, TypeError):
        raise ValueError("model_probe_pricing_missing_or_expired") from None
    # The prompt is fixed and tiny. 512 input tokens includes conservative chat
    # framing overhead; there are no tools, history, system prompt or retries.
    ceiling = (
        512 * quote.input_usd_per_million + limits.max_output_tokens * quote.output_usd_per_million
    ) / 1_000_000
    if ceiling > limits.max_cost_usd:
        raise ValueError("model_probe_budget_insufficient")
    return {
        **quote.model_dump(mode="json"),
        "estimated_ceiling_usd": str(ceiling),
        "input_token_allowance": 512,
    }


async def validate_selected_model(
    provider: Provider, credential: str | None, model: str, limits, quote: dict
) -> dict:
    """Exactly one model request. Only explicit validation calls this function."""
    from app.runtimes.probe import MODEL_TTL_SECONDS, fact

    def result(state, reason, **details):
        return {
            "fact": fact(state, reason, ttl=MODEL_TTL_SECONDS),
            "usage": None,
            "cost_usd": None,
            "requested_model": model,
            "max_requests": 1,
            "estimated_ceiling_usd": quote["estimated_ceiling_usd"],
            **details,
        }

    base = effective_base_url(provider)
    if not base:
        return result("failed", "provider_url_missing", requests_attempted=0)
    if not provider.enabled:
        return result("failed", "provider_disabled", requests_attempted=0)
    if not credential and provider.provider_type not in {
        ProviderType.custom.value,
        ProviderType.ollama.value,
    }:
        return result("failed", "provider_credential_missing", requests_attempted=0)
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "Reply only OK."}],
        "stream": False,
    }
    base = base.rstrip("/")
    if provider.provider_type == ProviderType.anthropic.value:
        url = f"{base}/messages" if base.endswith("/v1") else f"{base}/v1/messages"
        body["max_tokens"] = limits.max_output_tokens
    elif provider.provider_type == ProviderType.ollama.value:
        url = f"{base}/api/chat"
        body["options"] = {"num_predict": limits.max_output_tokens}
    else:
        url = f"{base}/chat/completions"
        body[
            "max_completion_tokens"
            if provider.provider_type == ProviderType.openai.value
            else "max_tokens"
        ] = limits.max_output_tokens
        if provider.provider_type == ProviderType.deepseek.value:
            body["thinking"] = {"type": "disabled"}
    try:
        async with asyncio.timeout(limits.timeout_seconds):
            async with httpx.AsyncClient(timeout=limits.timeout_seconds, trust_env=False) as client:
                response = await client.post(
                    url, headers=_auth_headers(provider, credential), json=body
                )
                response.raise_for_status()
                payload = response.json()
        if provider.provider_type == ProviderType.anthropic.value:
            content = "".join(
                item.get("text", "")
                for item in payload.get("content", [])
                if item.get("type") == "text"
            )
            usage = payload.get("usage") or {}
            input_tokens, output_tokens = usage.get("input_tokens"), usage.get("output_tokens")
        elif provider.provider_type == ProviderType.ollama.value:
            content = (payload.get("message") or {}).get("content")
            input_tokens, output_tokens = (
                payload.get("prompt_eval_count"),
                payload.get("eval_count"),
            )
        else:
            choices = payload.get("choices") or []
            content = ((choices[0].get("message") or {}).get("content")) if choices else None
            usage = payload.get("usage") or {}
            input_tokens, output_tokens = usage.get("prompt_tokens"), usage.get("completion_tokens")
        if not isinstance(content, str) or not content.strip():
            return result("failed", "model_response_invalid", requests_attempted=1)
        measured = all(type(n) is int and n >= 0 for n in (input_tokens, output_tokens))
        details = {"requests_attempted": 1}
        if measured:
            cost = (
                Decimal(input_tokens) * Decimal(quote["input_usd_per_million"])
                + Decimal(output_tokens) * Decimal(quote["output_usd_per_million"])
            ) / 1_000_000
            details.update(
                usage={"input_tokens": input_tokens, "output_tokens": output_tokens},
                cost_usd=str(cost),
            )
            if (
                input_tokens > 512
                or output_tokens > limits.max_output_tokens
                or cost > limits.max_cost_usd
            ):
                return result("failed", "provider_exceeded_probe_budget", **details)
        return result("passed", "selected_model_responded", **details)
    except (TimeoutError, httpx.TimeoutException):
        return result("unknown", "model_probe_timeout", requests_attempted=1)
    except httpx.HTTPStatusError as exc:
        return result(
            "failed",
            "selected_model_rejected",
            requests_attempted=1,
            http_status=exc.response.status_code,
        )
    except Exception:
        return result("unknown", "model_probe_transport_or_protocol_error", requests_attempted=1)


_TIMEOUT = 10.0


def _auth_headers(provider: Provider, credential: str | None) -> dict[str, str]:
    if not credential:
        return {}
    if provider.provider_type == ProviderType.anthropic.value:
        return {"x-api-key": credential, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {credential}"}


def _models_url(provider: Provider) -> str | None:
    base = effective_base_url(provider)
    if not base:
        return None
    base = base.rstrip("/")
    ptype = provider.provider_type
    if ptype == ProviderType.anthropic.value:
        return f"{base}/v1/models" if not base.endswith("/v1") else f"{base}/models"
    if ptype == ProviderType.ollama.value:
        return f"{base}/api/tags"
    if ptype == ProviderType.gemini.value and "generativelanguage" in base:
        return f"{base}/models"
    # OpenAI-compatible surface
    return f"{base}/models"


def _extract_models(provider: Provider, payload) -> list[str]:
    ptype = provider.provider_type
    try:
        if ptype == ProviderType.ollama.value:
            return [m["name"] for m in payload.get("models", []) if "name" in m]
        if ptype == ProviderType.anthropic.value:
            return [m["id"] for m in payload.get("data", []) if "id" in m]
        data = payload.get("data", payload.get("models", []))
        return [m["id"] for m in data if isinstance(m, dict) and "id" in m]
    except (AttributeError, TypeError):
        return []


async def test_provider(provider: Provider, credential: str | None) -> dict:
    """Minimal connectivity check. Returns {ok, latency_ms, error, models_count}."""
    url = _models_url(provider)
    if url is None:
        return {
            "ok": False,
            "latency_ms": None,
            "error": "no base_url for provider",
            "models_count": None,
        }
    if not credential and provider.provider_type not in {
        ProviderType.ollama.value,
        ProviderType.custom.value,
    }:
        return {
            "ok": False,
            "latency_ms": None,
            "error": "no credential stored",
            "models_count": None,
        }
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_auth_headers(provider, credential))
        latency = round((time.monotonic() - started) * 1000, 1)
        if response.status_code >= 400:
            # Never leak response bodies verbatim (may echo the key); redact anyway.
            return {
                "ok": False,
                "latency_ms": latency,
                "error": redact(f"HTTP {response.status_code}: {response.text[:200]}"),
                "models_count": None,
            }
        models = _extract_models(provider, response.json())
        return {"ok": True, "latency_ms": latency, "error": None, "models_count": len(models)}
    except Exception as exc:
        return {
            "ok": False,
            "latency_ms": round((time.monotonic() - started) * 1000, 1),
            "error": redact(str(exc)[:300]),
            "models_count": None,
        }


async def list_models(provider: Provider, credential: str | None) -> list[str]:
    url = _models_url(provider)
    if url is None:
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_auth_headers(provider, credential))
        if response.status_code >= 400:
            return []
        return _extract_models(provider, response.json())
    except Exception:
        return []


async def probe_models(provider: Provider, credential: str | None) -> dict:
    """单次抓取同时返回连接状态与模型清单（供"未保存配置先探测"用）。"""
    url = _models_url(provider)
    if url is None:
        return {"ok": False, "error": "no base_url for provider", "models": []}
    if provider.provider_type != ProviderType.ollama.value and not credential:
        return {"ok": False, "error": "no credential stored", "models": []}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, headers=_auth_headers(provider, credential))
        if response.status_code >= 400:
            return {
                "ok": False,
                "error": redact(f"HTTP {response.status_code}: {response.text[:200]}"),
                "models": [],
            }
        return {"ok": True, "error": None, "models": _extract_models(provider, response.json())}
    except Exception as exc:
        return {"ok": False, "error": redact(str(exc)[:300]), "models": []}
