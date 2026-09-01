"""Secret redaction (v0.2).

A process-wide registry of live secret values plus generic secret-shaped regex
patterns. ``redact(text)`` must be applied anywhere a secret could leak:
log streaming, WS/event payloads, and API responses. Registered values are
replaced by their mask (``sk-••••••••abcd``); pattern hits by a generic mask.
"""

import re
import threading

_MASK = "••••••••"

# Generic secret shapes (provider keys, bearer tokens, KEY=value env lines).
_PATTERNS: list[re.Pattern] = [
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI-style keys
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),  # Anthropic keys (also caught above)
    re.compile(r"sk-or-[A-Za-z0-9_\-]{8,}"),  # OpenRouter keys (also caught above)
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google/Gemini API keys
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),  # GitHub PATs
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)([A-Z][A-Z0-9_]*(?:API_KEY|APIKEY|TOKEN|SECRET))=([^\s'\"]{4,})"),
]


class RedactionRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}  # secret value -> mask

    def register(self, value: str | None) -> None:
        """Register a live secret value so redact() can scrub it verbatim."""
        if not value or len(value) < 4:
            return
        with self._lock:
            self._values[value] = mask_secret(value)

    def unregister(self, value: str | None) -> None:
        if not value:
            return
        with self._lock:
            self._values.pop(value, None)

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            items = list(self._values.items())
        for value, mask in items:
            if value in text:
                text = text.replace(value, mask)
        for pattern in _PATTERNS:
            text = pattern.sub(self._pattern_replacement, text)
        return text

    @staticmethod
    def _pattern_replacement(match: re.Match) -> str:
        if match.re is _PATTERNS[-1]:
            # KEY=value env shape: keep the variable name, mask the value.
            return f"{match.group(1)}={_MASK}"
        if match.group(0).startswith("Bearer "):
            return f"Bearer {_MASK}"
        return mask_secret(match.group(0))


registry = RedactionRegistry()


def register_secret(value: str | None) -> None:
    registry.register(value)


def unregister_secret(value: str | None) -> None:
    registry.unregister(value)


def redact(text: str) -> str:
    return registry.redact(text)


def redact_data(obj):
    """Recursively redact every string in a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {key: redact_data(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [redact_data(item) for item in obj]
    return obj


def mask_secret(value: str | None) -> str | None:
    """Render a display mask for a secret, e.g. ``sk-••••••••abcd``."""
    if not value:
        return None
    if len(value) <= 8:
        return _MASK
    prefix = value[:3] if value[:3].isalnum() or value.startswith("sk-") else ""
    return f"{prefix}{_MASK}{value[-4:]}"
