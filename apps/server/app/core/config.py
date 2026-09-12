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

    # M1 — economy（docs/m1-economy-design.md §30：金额/费用/比例全部配置化 + 政策版本）
    economy_policy_version: str = "econ-1"
    economy_starter_grant: int = 100_000
    economy_profile_reward: int = 500
    economy_company_profile_reward: int = 1_000
    economy_tutorial_reward: int = 2_000
    economy_achievement_reward: int = 1_500
    economy_daily_reward: int = 100
    economy_weekly_activity_reward: int = 500
    economy_recovery_grant: int = 1_000
    economy_recovery_threshold: int = 2_000
    economy_recovery_cooldown_hours: int = 24
    economy_official_reward_multiplier: float = 1.0
    economy_official_max_reward: int = 50_000  # 单笔官方任务上限（预算内发行）
    economy_official_outstanding_budget: int = 1_000_000  # 未结算官方任务总额上限
    economy_player_order_max_reward: int = 1_000_000  # 玩家订单单笔上限（花自己的钱，仍设护栏）
    economy_market_fee_bps: int = 500  # 基点：500 = 5%（挂牌/市场交易）
    economy_contract_fee_bps: int = 300  # 基点：300 = 3%（合同结算，从对价里扣）
    economy_fee_treasury_ratio: float = 0.6
    economy_fee_burn_ratio: float = 0.4
    economy_compute_credit_per_unit: int = 1  # 1 compute unit = 1 分钟 Agent 运行时长
    economy_training_credit_per_session: int = 200  # 培养成本：每个培养 session 的 CREDIT
    # M1.5 成本事件消费者（培养成本）默认关；测试/dev 显式开
    economy_cost_consumers_enabled: bool = False
    # M1.8 NPC 经济（deterministic：预算/阈值/节奏全部配置化）
    economy_npc_budget_injection: int = 50_000  # 单次预算注入额度（属于 mint，计入发行）
    economy_npc_budget_cap: int = 200_000  # 单个 NPC 累计注入上限
    economy_npc_max_price: int = 30_000  # NPC 单笔成交价上限
    economy_npc_fit_threshold_bps: int = 7_000  # fit 阈值（基点：7000 = 0.70）
    economy_npc_deals_per_round: int = 1  # 每轮每个 NPC 最多成交几单
    # M1.9：系统/管理只读与刷新面（默认关；v1 无 admin 角色体系，按需在 .env 打开）
    economy_admin_enabled: bool = False

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

    # 编排器的**自动派发**开关（默认开）。
    # 关掉之后 orchestrator 的 sweep 不再把 `todo` 任务派给 runtime，
    # 但它仍然可以在测试/调试里被显式驱动。测试默认关 —— 与
    # `position_access_sync` / `evidence_pipeline_enabled` 同一纪律：
    # **后台写者不和测试抢同一份 SQLite**（实测：争用会以 `database is locked` 出现，
    # 而且因为读→写升级的死锁语义，它**不**会等 busy timeout）。
    orchestrator_dispatch_enabled: bool = True

    # M2.3 —— 内部 Agent 工具执行面。写工具**只有**这一条通道（没有玩家 /tools 路由，T3）。
    # 调试口：允许 CLI / 测试从任意员工身份调用同一执行面（默认关；生产不开）。
    # 注意：即便打开，Authority / 领域校验**照做不误**（T4/T10）—— 它只是"谁能发起"，
    # 不是"可以绕过什么"。
    agent_tool_cli_enabled: bool = False

    # M2.2 —— 管理授权（Authority Projection，v41）。
    # `spend_credits` 的**默认**额度上限（整数最小单位）：冷启动种子把它写进
    # CEO 的授权行。金额只来自政策，**不**在代码里硬编码；政策调高后不会自动放大
    # 已存在的授权（改授权必须显式 revoke + grant —— 授权不该悄悄扩张）。
    authority_default_spend_limit: int = 50_000
    # M2.2 —— 任职变化时广播 `role.context_available` / `role.context_withdrawn`
    # （纯事实通知，不含"请去学习"的系统指令）。测试默认关（conftest），
    # 由专门测试直接调用 handle() 覆盖。
    role_context_events: bool = True

    # M2.1 —— 规划 fixture（D3/M2-ADR-12，W33）：**测试/教程/CI/演示基础设施**，
    # 不是产品模式。开启后，项目可以**显式**请求 `planning_fixture=deterministic_template`，
    # 用固定模板生成确定性执行图（让 CI/golden path 不依赖 LLM Manager Agent）。
    # 默认 False：生产项目**永远不能**隐式落到确定性模板上。
    allow_planning_fixtures: bool = False

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
