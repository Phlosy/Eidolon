"""Hermes provider configurator (v0.2).

Per the verified deployment facts (docs/research.md §3/§12): the model block
lives in ``config.yaml`` and secrets live in ``.env`` under the profile home —
the employee volume mounted at ``/opt/data``. The ``.env`` file is written
with mode 0600. Anything non-native maps to ``provider: custom`` + ``base_url``.
"""

import yaml

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

# Eidolon provider type -> native hermes provider name (None = use "custom").
_HERMES_PROVIDER: dict[str, str | None] = {
    ProviderType.anthropic.value: "anthropic",
    ProviderType.openrouter.value: "openrouter",
    ProviderType.deepseek.value: "deepseek",
    ProviderType.gemini.value: "gemini",
    ProviderType.openai.value: None,  # OpenAI goes through custom + base_url
    ProviderType.ollama.value: None,  # Ollama's OpenAI-compatible /v1 surface
    ProviderType.custom.value: None,
}


class HermesProviderConfigurator(RuntimeProviderConfigurator):
    runtime_type = RuntimeType.hermes.value

    def supported_providers(self) -> list[str]:
        return SUPPORTED

    def validate_provider(self, provider: Provider) -> list[str]:
        errors: list[str] = []
        if provider.provider_type not in SUPPORTED:
            errors.append(f"unsupported provider type: {provider.provider_type}")
        native = _HERMES_PROVIDER.get(provider.provider_type)
        if native is None and not effective_base_url(provider):
            errors.append("custom-style providers require a base_url")
        if provider.provider_type != ProviderType.ollama.value and not provider.credential_ref:
            errors.append("provider has no stored credential")
        return errors

    def render_provider_config(
        self, provider: Provider, model: str, credential: str | None
    ) -> ProviderRenderResult:
        self.supported_or_raise(provider)
        native = _HERMES_PROVIDER.get(provider.provider_type)
        base_url = effective_base_url(provider)
        if provider.provider_type == ProviderType.ollama.value and base_url:
            base_url = base_url.rstrip("/") + "/v1"

        model_block: dict = {"provider": native or "custom", "model": model}
        if native is None:
            if base_url:
                model_block["base_url"] = base_url.rstrip("/")
            # custom providers read the key from the model block; "none" when keyless
            model_block["api_key"] = credential or "none"
        config = yaml.safe_dump({"model": model_block}, sort_keys=False)

        env_lines: list[str] = []
        key_env = key_env_for(provider)
        if credential and key_env and native is not None:
            env_lines.append(f"{key_env}={credential}")
        return ProviderRenderResult(
            env={},
            files={"config.yaml": config, ".env": "\n".join(env_lines) + "\n"},
        )

    async def test_provider(self, provider: Provider, credential: str | None) -> dict:
        return await probe.test_provider(provider, credential)

    async def list_models(self, provider: Provider, credential: str | None) -> list[str]:
        return await probe.list_models(provider, credential)
