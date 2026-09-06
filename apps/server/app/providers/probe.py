"""Provider probing (v0.2): connectivity test + model listing against provider APIs.

Used by ProviderService and by the runtime provider configurators. Network
failures degrade to ``ok=False`` / empty model lists — never raise.
"""

import time

import httpx

from app.core.redaction import redact
from app.models.enums import ProviderType
from app.models.provider import Provider
from app.providers.base import effective_base_url

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
    if provider.provider_type == ProviderType.ollama.value:
        pass  # ollama needs no credential
    elif not credential:
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
