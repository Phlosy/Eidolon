"""RuntimeProviderConfigurator ABC + shared provider metadata (v0.2).

A configurator knows how to wire a Provider (credentials + model) into a
specific runtime family: which files to render into the employee volume and
which env vars to inject at container create time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import ProviderType
from app.models.provider import Provider

# Default base URLs per provider type (None = no HTTP base, e.g. local ollama).
DEFAULT_BASE_URLS: dict[str, str] = {
    ProviderType.openai.value: "https://api.openai.com/v1",
    ProviderType.anthropic.value: "https://api.anthropic.com",
    ProviderType.openrouter.value: "https://openrouter.ai/api/v1",
    ProviderType.deepseek.value: "https://api.deepseek.com/v1",
    ProviderType.gemini.value: "https://generativelanguage.googleapis.com/v1beta/openai",
    ProviderType.ollama.value: "http://localhost:11434",
}

# Env var used to hand the API key to a runtime container.
PROVIDER_KEY_ENV: dict[str, str] = {
    ProviderType.openai.value: "OPENAI_API_KEY",
    ProviderType.anthropic.value: "ANTHROPIC_API_KEY",
    ProviderType.openrouter.value: "OPENROUTER_API_KEY",
    ProviderType.deepseek.value: "DEEPSEEK_API_KEY",
    ProviderType.gemini.value: "GEMINI_API_KEY",
    ProviderType.custom.value: "OPENAI_API_KEY",
}


def effective_base_url(provider: Provider) -> str | None:
    return provider.base_url or DEFAULT_BASE_URLS.get(provider.provider_type)


def key_env_for(provider: Provider) -> str | None:
    return PROVIDER_KEY_ENV.get(provider.provider_type)


@dataclass
class ProviderRenderResult:
    """Rendered provider configuration for one runtime instance."""

    env: dict[str, str] = field(default_factory=dict)  # container env (incl. API key)
    files: dict[str, str] = field(default_factory=dict)  # relative path -> content
    notes: list[str] = field(default_factory=list)


class RuntimeProviderConfigurator(ABC):
    runtime_type: str

    @abstractmethod
    def supported_providers(self) -> list[str]: ...

    @abstractmethod
    def validate_provider(self, provider: Provider) -> list[str]:
        """Return a list of validation errors ([] = valid)."""
        ...

    @abstractmethod
    def render_provider_config(
        self, provider: Provider, model: str, credential: str | None
    ) -> ProviderRenderResult: ...

    def supported_or_raise(self, provider: Provider) -> None:
        if provider.provider_type not in self.supported_providers():
            raise ValueError(
                f"provider type {provider.provider_type!r} not supported by {self.runtime_type}"
            )
