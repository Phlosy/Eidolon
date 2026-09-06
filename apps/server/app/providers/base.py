"""RuntimeProviderConfigurator ABC + shared provider metadata (v0.2).

A configurator knows how to wire a Provider (credentials + model) into a
specific runtime family: which files to render into the employee volume and
which env vars to inject at container create time.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import ProviderType
from app.models.provider import Provider


@dataclass(frozen=True)
class ProviderPreset:
    """内置厂商预设：用户只选厂商 + 填 key，base_url 与模型候选由预设带出。

    这是厂商元数据的**唯一事实来源**：DEFAULT_BASE_URLS 由它派生，前端通过
    GET /providers/presets 拿到同一份清单，不再各自硬编码。

    ``recommended_models`` 只是"常见示例"——厂商模型会更新，硬编码必然过期；
    权威的模型清单来自对厂商 /models 接口的实时探测（POST /providers/probe），
    示例只在探测不可用（没填 key、网络不通）时兜底。``docs_url`` 指向官方模型
    文档，供用户核对。
    """

    provider_type: str
    default_base_url: str | None
    requires_api_key: bool
    recommended_models: tuple[str, ...] = ()
    docs_url: str | None = None


PROVIDER_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        ProviderType.openai.value,
        "https://api.openai.com/v1",
        True,
        ("gpt-4o", "gpt-4o-mini"),
        "https://platform.openai.com/docs/models",
    ),
    ProviderPreset(
        ProviderType.anthropic.value,
        "https://api.anthropic.com",
        True,
        ("claude-sonnet-4-5", "claude-opus-4-1", "claude-haiku-4-5"),
        "https://docs.anthropic.com/en/docs/about-claude/models",
    ),
    ProviderPreset(
        ProviderType.deepseek.value,
        "https://api.deepseek.com/v1",
        True,
        ("deepseek-chat", "deepseek-reasoner"),
        "https://api-docs.deepseek.com/",
    ),
    ProviderPreset(
        ProviderType.moonshot.value,
        "https://api.moonshot.cn/v1",
        True,
        ("kimi-k2-0711-preview", "moonshot-v1-32k", "moonshot-v1-128k"),
        "https://platform.moonshot.cn/docs/intro",
    ),
    ProviderPreset(
        ProviderType.zhipu.value,
        "https://open.bigmodel.cn/api/paas/v4",
        True,
        ("glm-4.6", "glm-4.5", "glm-4.5-air"),
        "https://docs.bigmodel.cn/cn/guide/models",
    ),
    ProviderPreset(
        ProviderType.qwen.value,
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        True,
        ("qwen-max", "qwen-plus", "qwen-turbo"),
        "https://help.aliyun.com/zh/model-studio/getting-started/models",
    ),
    ProviderPreset(
        ProviderType.groq.value,
        "https://api.groq.com/openai/v1",
        True,
        ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"),
        "https://console.groq.com/docs/models",
    ),
    ProviderPreset(
        ProviderType.mistral.value,
        "https://api.mistral.ai/v1",
        True,
        ("mistral-large-latest", "mistral-small-latest"),
        "https://docs.mistral.ai/getting-started/models/",
    ),
    ProviderPreset(
        ProviderType.openrouter.value,
        "https://openrouter.ai/api/v1",
        True,
        ("openai/gpt-4o", "anthropic/claude-sonnet-4.5", "deepseek/deepseek-chat"),
        "https://openrouter.ai/models",
    ),
    ProviderPreset(
        ProviderType.gemini.value,
        "https://generativelanguage.googleapis.com/v1beta/openai",
        True,
        ("gemini-2.5-pro", "gemini-2.5-flash"),
        "https://ai.google.dev/gemini-api/docs/models",
    ),
    ProviderPreset(
        ProviderType.ollama.value,
        "http://localhost:11434",
        False,  # 本地运行，无需 key
        ("llama3.1", "qwen3"),
        "https://ollama.com/library",
    ),
    # 自定义 OpenAI 兼容服务：base_url 必填，key 视目标服务而定
    ProviderPreset(ProviderType.custom.value, None, False, ()),
)

PRESETS_BY_TYPE: dict[str, ProviderPreset] = {p.provider_type: p for p in PROVIDER_PRESETS}

# Default base URLs per provider type (None = no HTTP base, e.g. local ollama).
DEFAULT_BASE_URLS: dict[str, str] = {
    p.provider_type: p.default_base_url for p in PROVIDER_PRESETS if p.default_base_url
}

# Env var used to hand the API key to a runtime container.
PROVIDER_KEY_ENV: dict[str, str] = {
    ProviderType.openai.value: "OPENAI_API_KEY",
    ProviderType.anthropic.value: "ANTHROPIC_API_KEY",
    ProviderType.openrouter.value: "OPENROUTER_API_KEY",
    ProviderType.deepseek.value: "DEEPSEEK_API_KEY",
    ProviderType.moonshot.value: "MOONSHOT_API_KEY",
    ProviderType.zhipu.value: "ZHIPU_API_KEY",
    ProviderType.qwen.value: "DASHSCOPE_API_KEY",
    ProviderType.groq.value: "GROQ_API_KEY",
    ProviderType.mistral.value: "MISTRAL_API_KEY",
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
