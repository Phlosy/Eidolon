"""v2 settings. Prefix ``EIDOLON_V2_`` keeps them apart from the legacy ``app`` settings
while both packages live in one repository (strangler period, see v2-ADR-1)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EIDOLON_V2_", env_file=".env", extra="ignore")

    # PostgreSQL is the only supported database (v2-architecture.md §0, V1).
    database_url: str = "postgresql+psycopg://eidolon:eidolon@127.0.0.1:5433/eidolon"
    # Largest fact payload stored inline, in bytes of canonical JSON. Anything larger
    # belongs in object storage with a reference in the fact (K4).
    fact_payload_max_bytes: int = 64 * 1024
    # NOTIFY channel used to wake workers after a fact commits.
    fact_notify_channel: str = "eidolon_facts"
    # Process logging (eidolon.logging): JSON lines on stderr at this level.
    log_level: str = "INFO"
    # Worker: fall back to polling every N seconds if no NOTIFY arrives; facts per batch.
    worker_poll_interval: float = 5.0
    worker_batch_limit: int = 200

    # Content-addressed transcript store (full contexts / responses / tool results);
    # facts carry only hashes and summaries (v2-architecture §4.2). Relative to apps/server.
    transcript_dir: str = "data/transcripts"
    # Per-task sandbox workspaces (R3-d): <root>/<company>/<project>/<task>; relative to
    # apps/server.
    workspace_root: str = "data/workspaces"
    # Who reviews (until identity/employment lands in R4): the person id and role of the QA agent.
    reviewer_person: str = "qa-1"
    reviewer_role: str = "qa"

    # -- model providers (eidolon.agent.providers) ---------------------------------
    # kind: openai_compatible | anthropic. mode: live | record | replay (v2-arch §4.6).
    provider_kind: str = "openai_compatible"
    provider_mode: str = "live"
    # Where record/replay fixtures live. Relative paths resolve against apps/server.
    provider_fixture_dir: str = "tests_v2/fixtures/providers"
    provider_timeout_s: float = 120.0
    provider_max_attempts: int = 3
    # openai_compatible: DeepSeek is the default verification target (V2).
    provider_openai_compat_base_url: str = "https://api.deepseek.com"
    provider_openai_compat_api_key: str | None = None
    provider_openai_compat_model: str = "deepseek-flash"
    # anthropic (official SDK). fallbacks=True sends `fallbacks: "default"` via the beta
    # endpoint so a safety refusal is retried on a fallback model inside the same call.
    provider_anthropic_api_key: str | None = None
    provider_anthropic_base_url: str | None = None
    provider_anthropic_model: str = "claude-opus-5-5"
    provider_anthropic_fallbacks: bool = False

    # API (R5-b): session cookie (HttpOnly) + eidolon_csrf (readable, double-submit).
    session_cookie_name: str = "eidolon_session"
    session_ttl_hours: int = 24 * 30
    cookie_secure: bool = False
    cors_origins: str = ""  # comma-separated; empty = same origin only
    api_host: str = "127.0.0.1"
    api_port: int = 26881


settings = Settings()
