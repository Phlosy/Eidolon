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

    # Behavioral Policy v1（docs/employee-brain-behavior-policy.md）总开关。
    # 关闭后 resolve() 永远返回 DEFAULT_POLICY —— 逐字等于引入 traits 之前的行为，
    # 这是 §3.4 验收条件 5 的回滚语义锚点。
    behavior_policy_enabled: bool = True

    # P4d — 职位层权限的事件收敛（docs/position-system.md §4）。
    # 关掉之后：`employee.position_*` 事件不再触发 Desired State 收敛，
    # 启动补收敛也不跑 —— 任职照常生效，只是职位包不会自动加减（回滚锚点）。
    position_access_sync: bool = True

    # Provisioning（P4d/v0.4）：单步执行超时（秒）。外部资源（gitea http、runtime
    # 进程启动等）万一挂起，超时后该步强制 failed 并记录原因 —— 绝不无限 running；
    # 配套启动补收敛（workforce/access.sweep_stale_provisioning_jobs）兜底进程死亡。
    provisioning_step_timeout_seconds: float = 120.0

    # P6 — 真实工作 → Evidence → Assessment 的自动流水线（docs/evidence-pipeline.md）。
    # 关掉之后：业务事件不再自动收证据、项目结束不自动跑考核（回滚锚点）。
    # 测试默认关闭（conftest），由专门的 pipeline 测试显式开启 —— 避免像 P4d 那样
    # 后台消费者跟测试抢同一份状态。
    evidence_pipeline_enabled: bool = True

    # P11 —— 自主学习（LearningSession）。默认关：真实 Provider 会消耗额度，
    # 需要用户显式开启（公司/员工可覆盖）。force_failure 仅供测试注入。
    autonomous_learning_enabled: bool = False
    learning_force_failure: bool = False

    # v0.7 — human user authentication. Sessions are opaque, hashed server-side,
    # and transported only in an HttpOnly cookie.
    auth_required: bool = True
    session_cookie_name: str = "eidolon_session"
    session_ttl_hours: int = 24 * 30
    cookie_secure: bool = False
    email_delivery_mode: str = "console"  # console (local development) | smtp
    email_verification_ttl_minutes: int = 30
    web_app_url: str = "http://127.0.0.1:26880"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_starttls: bool = True
    smtp_use_ssl: bool = False
    webauthn_challenge_ttl_minutes: int = 5
    webauthn_rp_id: str = "127.0.0.1"
    webauthn_rp_name: str = "Eidolon"
    webauthn_origin: str = "http://127.0.0.1:26880"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
