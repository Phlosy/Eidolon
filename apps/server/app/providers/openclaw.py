"""OpenClaw provider configurator (v0.2).

Per the verified deployment facts (docs/research.md §8): the default model is
``agents.defaults.model`` ("provider/model") in ``openclaw.json`` and provider
keys are injected as container env vars at create time (never written into
git-tracked config). Model listing prefers the admin RPC (``models.list``) and
falls back to the provider's own API.
"""

import json

import httpx

from app.models.enums import ProviderType, RuntimeType
from app.models.provider import Provider
from app.providers import probe
from app.providers.base import (
    ProviderRenderResult,
    RuntimeProviderConfigurator,
    effective_base_url,
    key_env_for,
)

SUPPORTED = [p.value for p in ProviderType]


class OpenClawProviderConfigurator(RuntimeProviderConfigurator):
    runtime_type = RuntimeType.openclaw.value

    def supported_providers(self) -> list[str]:
        return SUPPORTED

    def validate_provider(self, provider: Provider) -> list[str]:
        errors: list[str] = []
        if provider.provider_type not in SUPPORTED:
            errors.append(f"unsupported provider type: {provider.provider_type}")
        if provider.provider_type in (ProviderType.custom.value,) and not effective_base_url(
            provider
        ):
            errors.append("custom providers require a base_url")
        if provider.provider_type != ProviderType.ollama.value and not provider.credential_ref:
            errors.append("provider has no stored credential")
        return errors

    def render_provider_config(
        self, provider: Provider, model: str, credential: str | None
    ) -> ProviderRenderResult:
        self.supported_or_raise(provider)
        config: dict = {
            "agents": {"defaults": {"model": f"{provider.provider_type}/{model}"}},
            # gateway.mode=local is required or the gateway exits with
            # "Missing config"; the per-employee token is rendered into the
            # (private, container-only) config by render_with_context()
            "gateway": {"mode": "local", "auth": {"mode": "token"}},
            # admin-http-rpc is only reachable on the private runtime network
            "plugins": {"entries": {"admin-http-rpc": {"enabled": True}}},
        }
        base_url = effective_base_url(provider)
        if provider.provider_type == ProviderType.custom.value and base_url:
            config["models"] = {
                "providers": {
                    "custom": {"baseUrl": base_url.rstrip("/"), "api": "openai-completions"}
                }
            }
        env: dict[str, str] = {}
        key_env = key_env_for(provider)
        if credential and key_env:
            env[key_env] = credential
        return ProviderRenderResult(
            env=env,
            files={"openclaw.json": json.dumps(config, indent=2) + "\n"},
        )

    def render_with_context(
        self,
        provider: Provider | None,
        model: str | None,
        credential: str | None,
        *,
        gateway_token: str,
    ) -> ProviderRenderResult:
        """Full render including the gateway token (only ever written into the
        employee volume, never persisted by Eidolon)."""
        if provider is not None and model:
            result = self.render_provider_config(provider, model, credential)
            config = json.loads(result.files["openclaw.json"])
        else:
            result = ProviderRenderResult()
            config = {"gateway": {}, "plugins": {"entries": {"admin-http-rpc": {"enabled": True}}}}
        config["gateway"] = {
            **config.get("gateway", {}),
            "mode": "local",
            "auth": {"mode": "token", "token": gateway_token},
        }
        config.setdefault("agents", {"defaults": {}})
        result.files["openclaw.json"] = json.dumps(config, indent=2) + "\n"
        return result

    async def list_models_via_admin_rpc(self, gateway_url: str, token: str) -> list[str]:
        """models.list through the admin-http-rpc plugin (private network only)."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{gateway_url.rstrip('/')}/api/v1/admin/rpc",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"id": "models", "method": "models.list", "params": {}},
                )
            if response.status_code >= 400:
                return []
            payload = response.json()
            result = payload.get("result", payload)
            models = result.get("models", result) if isinstance(result, dict) else result
            out = []
            for m in models or []:
                out.append(
                    m.get("id") or m.get("name") or str(m) if isinstance(m, dict) else str(m)
                )
            return out
        except Exception:
            return []

    async def test_provider(self, provider: Provider, credential: str | None) -> dict:
        return await probe.test_provider(provider, credential)

    async def list_models(self, provider: Provider, credential: str | None) -> list[str]:
        return await probe.list_models(provider, credential)
