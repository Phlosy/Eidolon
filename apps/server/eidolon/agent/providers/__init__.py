"""Model providers: one protocol, two live implementations, record/replay, and the
fact that every call leaves behind. ``build_provider`` assembles the configured
stack: live provider → (record | replay) → ledgered.
"""

import os
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from eidolon.agent.providers.accounting import Ledgered, ModelCallLedger
from eidolon.agent.providers.base import (
    CallContext,
    ChatEvent,
    ChatRequest,
    Completed,
    Message,
    Provider,
    ProviderError,
    ProviderTimeout,
    RetryPolicy,
    TextDelta,
    ToolCall,
    ToolCallEvent,
    ToolResult,
    ToolSpec,
    Usage,
)
from eidolon.agent.providers.recorder import FixtureMissing, Recording, Replaying
from eidolon.config import Settings, settings

SERVER_ROOT = Path(__file__).resolve().parents[3]
KINDS = ("openai_compatible", "anthropic")
MODES = ("live", "record", "replay")


def default_model(cfg: Settings = settings) -> str:
    if cfg.provider_kind == "anthropic":
        return cfg.provider_anthropic_model
    return cfg.provider_openai_compat_model


def live_provider(cfg: Settings = settings, *, kind: str | None = None) -> Provider:
    kind = kind or cfg.provider_kind
    retry = RetryPolicy(max_attempts=cfg.provider_max_attempts)
    if kind == "openai_compatible":
        from eidolon.agent.providers.openai_compatible import OpenAICompatible

        return OpenAICompatible(
            base_url=cfg.provider_openai_compat_base_url,
            api_key=cfg.provider_openai_compat_api_key or os.environ.get("DEEPSEEK_API_KEY"),
            timeout=cfg.provider_timeout_s,
            retry=retry,
        )
    if kind == "anthropic":
        from eidolon.agent.providers.anthropic import Anthropic

        return Anthropic(
            api_key=cfg.provider_anthropic_api_key or os.environ.get("ANTHROPIC_API_KEY"),
            base_url=cfg.provider_anthropic_base_url,
            timeout=cfg.provider_timeout_s,
            max_retries=max(cfg.provider_max_attempts - 1, 0),
            fallbacks=cfg.provider_anthropic_fallbacks,
        )
    raise ValueError(f"unknown provider kind {kind!r}; known: {KINDS}")


def fixture_dir(cfg: Settings = settings) -> Path:
    path = Path(cfg.provider_fixture_dir)
    return path if path.is_absolute() else SERVER_ROOT / path


def build_provider(
    cfg: Settings = settings,
    *,
    session_factory: sessionmaker | None = None,
    mode: str | None = None,
    kind: str | None = None,
) -> Provider:
    mode = mode or cfg.provider_mode
    if mode not in MODES:
        raise ValueError(f"unknown provider mode {mode!r}; known: {MODES}")
    provider: Provider
    if mode == "replay":
        provider = Replaying(fixture_dir(cfg))
    else:
        provider = live_provider(cfg, kind=kind)
        if mode == "record":
            provider = Recording(provider, fixture_dir(cfg))
    if session_factory is not None:
        provider = Ledgered(provider, ModelCallLedger(session_factory), mode=mode)
    return provider


__all__ = [
    "CallContext",
    "ChatEvent",
    "ChatRequest",
    "Completed",
    "FixtureMissing",
    "Ledgered",
    "Message",
    "ModelCallLedger",
    "Provider",
    "ProviderError",
    "ProviderTimeout",
    "Recording",
    "Replaying",
    "RetryPolicy",
    "TextDelta",
    "ToolCall",
    "ToolCallEvent",
    "ToolResult",
    "ToolSpec",
    "Usage",
    "build_provider",
    "default_model",
    "fixture_dir",
    "live_provider",
]
