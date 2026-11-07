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


settings = Settings()
