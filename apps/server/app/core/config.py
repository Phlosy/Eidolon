"""Application configuration (pydantic-settings, EIDOLON_ prefix). See docs/architecture.md §9."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EIDOLON_", env_file=".env", extra="ignore")

    api_host: str = "127.0.0.1"  # loopback for host dev; 0.0.0.0 inside docker (internal net)
    api_port: int = 26881
    web_port: int = 26880
    metrics_port: int = 26890  # reserved
    runtime_debug_port_start: int = 26900  # reserved
    runtime_debug_port_end: int = 26999  # reserved
    database_url: str = "sqlite:///./data/eidolon.db"
    workspace_root: str = "./data/workspaces"
    data_root: str = "./data"  # data/employees/{id}/..., data/projects/{id}/...
    runtime_mode: str = "mock"  # mock | auto
    mock_task_seconds: float = 8.0
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:26880"
    company_name: str = "Eidolon Studio"

    # v0.2 — persistent workforce
    secret_key: str = "change-me-in-production"  # derives the local secret-store Fernet key
    runtime_healthcheck_interval: float = 30.0  # seconds between container health polls
    update_check_enabled: bool = True
    update_check_interval: int = 21600  # seconds
    update_policy: str = "notify_only"  # notify_only | managed | automatic (latter reserved)
    hermes_image: str = "nousresearch/hermes-agent:latest"
    openclaw_image: str = "ghcr.io/openclaw/openclaw:latest"
    runtime_network: str = "eidolon-runtime-net"

    # v0.3 phase 2 — optional builtin Gitea (manual one-click install)
    gitea_image: str = "gitea/gitea:1"

    # v0.4 — employee lifecycle
    # Admin API token for the builtin Gitea (a fresh install has no admin account;
    # lifecycle git steps fail with "gitea admin not configured" until this is set).
    gitea_admin_token: str | None = None
    # dev/test escape hatch: allow DELETE /employees/{id} (business UI uses offboarding)
    allow_hard_delete: bool = False
    # Production starts as a newly founded, zero-employee company. The legacy
    # autonomous five-role team remains opt-in for development fixtures.
    seed_demo_workforce: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
