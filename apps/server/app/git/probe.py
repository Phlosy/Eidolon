"""Git platform connectivity probe (v0.3 phase 2).

Tests reachability of an external git platform's REST API. Each platform type
has its own version endpoint and auth style:

- gitlab: GET {base}/api/v4/version with ``PRIVATE-TOKEN`` header
- gitea:  GET {base}/api/v1/version with ``Authorization: token <tok>``
- github: GET {base}/api/v3/meta (GitHub Enterprise), falling back to /api/v3,
  with ``Authorization: Bearer <tok>``
- custom: tries {base}/api/v1/version (gitea-style) then /api/v4/version
  (gitlab-style)

Network failures degrade to ``ok=False`` — never raise. Errors are redacted
so a token echoed by a server or exception can never leak.
"""

import time

import httpx

from app.core.redaction import redact
from app.models.enums import GitPlatformType

_TIMEOUT = 5.0


def _targets(platform_type: str, base_url: str, token: str | None) -> list[tuple[str, dict]]:
    """Ordered (url, headers) attempts for the platform."""
    base = base_url.rstrip("/")
    if platform_type == GitPlatformType.gitlab.value:
        headers = {"PRIVATE-TOKEN": token} if token else {}
        return [(f"{base}/api/v4/version", headers)]
    if platform_type == GitPlatformType.gitea.value:
        headers = {"Authorization": f"token {token}"} if token else {}
        return [(f"{base}/api/v1/version", headers)]
    if platform_type == GitPlatformType.github.value:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        return [(f"{base}/api/v3/meta", headers), (f"{base}/api/v3", headers)]
    # custom: gitea-style first, then gitlab-style
    attempts = []
    attempts.append(
        (f"{base}/api/v1/version", {"Authorization": f"token {token}"} if token else {})
    )
    attempts.append((f"{base}/api/v4/version", {"PRIVATE-TOKEN": token} if token else {}))
    return attempts


def _extract_version(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    version = payload.get("version") or payload.get("installed_version")
    return str(version) if version else None


async def test_connection(platform_type: str, base_url: str | None, token: str | None) -> dict:
    """Reach the platform API. Returns {ok, latency_ms, version, error}."""
    if not base_url:
        return {"ok": False, "latency_ms": None, "version": None, "error": "no base_url"}
    started = time.monotonic()
    last_error: str | None = None
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for url, headers in _targets(platform_type, base_url, token):
                try:
                    response = await client.get(url, headers=headers)
                except Exception as exc:
                    last_error = redact(str(exc)[:300])
                    continue
                latency = int(round((time.monotonic() - started) * 1000))
                if response.status_code >= 400:
                    # Never leak response bodies verbatim (may echo the token).
                    last_error = redact(f"HTTP {response.status_code}: {response.text[:200]}")
                    continue
                try:
                    version = _extract_version(response.json())
                except Exception:
                    version = None
                return {"ok": True, "latency_ms": latency, "version": version, "error": None}
    except Exception as exc:
        last_error = redact(str(exc)[:300])
    return {
        "ok": False,
        "latency_ms": int(round((time.monotonic() - started) * 1000)),
        "version": None,
        "error": last_error or "unreachable",
    }
