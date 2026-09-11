"""工作模式默认值与规划 fixture 门控（M2.1，D2 / D3，W33 / W35）。

两件**产品配置**，一件**基础设施门控**：

| 关注点 | 位置 | 语义 |
| --- | --- | --- |
| 公司默认工作模式 | `Company.settings` 的 `work_mode_default` | 新项目用哪种模式（冷启动 → 成熟） |
| Work Intake 责任目标 | `Company.settings` 的 `work_routing` | 哪个职位承担接收工作 |
| 规划 fixture | `Settings.allow_planning_fixtures` | **基础设施**，只能显式启用（W33） |

纪律：

1. **`work_mode` 是项目级快照**（W35）：项目创建时写入 `projects.work_mode`，
   之后公司默认值怎么变都不改写它 —— 执行中的语义不会漂移。
2. **`guided` 与 `managed` 的差别只有 human involvement**（W36）：两种模式下
   「接不接 / 怎么拆 / 选谁 / 是否返工 / 是否交付」都来自 Manager Agent 或 Human Owner。
3. **永不隐式启用 fixture**（W33）：不存在 "Manager 没反应 → 用模板顶上" 这条路径。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.events.bus import bus
from app.models.enums import PlanningFixture, ProjectWorkMode, ResponsibilityKind
from app.models.organization import Company
from app.repositories import organization as org_repo
from app.work import contracts as C

logger = logging.getLogger(__name__)


class WorkPolicyError(ValueError):
    """工作策略输入非法（未知模式 / 未开启的 fixture）。"""


def company_work_mode_default(db: Session, company: Company) -> ProjectWorkMode:
    """公司当前默认工作模式。

    优先级：公司显式覆盖 → 公司阶段默认（`WORK_MODE_BY_COMPANY_STAGE`）。
    刻意**不**看"是否已有完成过的项目"—— 那个判断只用于**推进**默认值
    （`promote_work_mode_after_onboarding`），不在读取时动态改答案，
    否则同一家公司在两个时刻会给出不同默认值，用户无法解释。
    """
    raw = (company.settings or {}).get(C.WORK_MODE_SETTINGS_KEY) or {}
    configured = str(raw.get("work_mode") or "").strip().lower()
    if configured:
        try:
            return ProjectWorkMode(configured)
        except ValueError as exc:
            raise WorkPolicyError(f"unknown work_mode: {configured!r}") from exc
    return C.default_work_mode_for_stage(company.stage)


def is_work_mode_explicitly_configured(company: Company) -> bool:
    raw = (company.settings or {}).get(C.WORK_MODE_SETTINGS_KEY) or {}
    return bool(str(raw.get("work_mode") or "").strip())


def set_company_work_mode_default(
    db: Session, company: Company, mode: ProjectWorkMode, *, reason: str, commit: bool = True
) -> ProjectWorkMode:
    """显式设定公司默认工作模式（Settings 页面 / Owner 操作）。

    只改**默认**，不改任何既有项目（W35）。
    """
    settings_data = dict(company.settings or {})
    block = dict(settings_data.get(C.WORK_MODE_SETTINGS_KEY) or {})
    before = company_work_mode_default(db, company)
    if before is mode and block.get("work_mode") == mode.value:
        return before
    block.update({"work_mode": mode.value, "reason": reason})
    settings_data[C.WORK_MODE_SETTINGS_KEY] = block
    company.settings = settings_data
    if commit:
        db.commit()
        bus.publish(
            "company.work_default_changed",
            {"from": before.value, "to": mode.value, "reason": reason},
            company_id=int(company.id),
        )
    return mode


def set_company_work_intake_code(
    db: Session, company: Company, position_code: str, *, commit: bool = True
) -> str:
    """显式设定承担 Work Intake 责任的职位 code（D1：公司可配，默认 CEO）。

    只改**责任目标**，不改任何既有项目（项目的 `work_intake_position_code` 是快照）。
    空字符串 = 移除覆盖、回到默认。
    """
    settings_data = dict(company.settings or {})
    routing = dict(settings_data.get(C.RESPONSIBILITY_SETTINGS_KEY) or {})
    code = position_code.strip()
    if code:
        routing[ResponsibilityKind.work_intake.value] = code
    else:
        routing.pop(ResponsibilityKind.work_intake.value, None)
    settings_data[C.RESPONSIBILITY_SETTINGS_KEY] = routing
    company.settings = settings_data
    resolved, _ = (code or C.RESPONSIBILITY_DEFAULTS[ResponsibilityKind.work_intake], False)
    if commit:
        db.commit()
        bus.publish(
            "company.work_routing_changed",
            {"responsibility": ResponsibilityKind.work_intake.value, "position_code": resolved},
            company_id=int(company.id),
        )
    return resolved


def promote_after_project_completion(db: Session, company_id: int) -> bool:
    """项目完成时的便捷入口（只取 company_id，供 orchestrator / 交付域调用）。

    两个完成点（`orchestrator._advance` 的 final_review、`project_delivery.complete_phase`
    的 delivery 阶段）共用本函数，避免"两处各写一遍推进逻辑"漂移。
    """
    company = org_repo.get_company(db, int(company_id))
    if company is None:  # pragma: no cover - 防御
        return False
    promoted = promote_work_mode_after_onboarding(db, company)
    if promoted:
        logger.info("公司默认工作模式推进为 managed（guided onboarding 完成）")
    return promoted


def promote_work_mode_after_onboarding(db: Session, company: Company) -> bool:
    """冷启动学习期结束：把公司默认从 `guided` 推进到 `managed`（D2/B7）。

    调用点是"首次真实项目走完"（`project.completed`）。它是**产品默认值**的推进，
    不是工作决策 —— 它不改任何项目的 `work_mode` 快照，也不替公司决定任何事。

    幂等：已显式配置过默认值的公司**永不**被自动改写（尊重用户选择）；
    已经是 managed 时不做任何事。返回是否真的发生了推进。
    """
    if is_work_mode_explicitly_configured(company):
        return False
    if company_work_mode_default(db, company) is C.WORK_MODE_AFTER_ONBOARDING:
        return False
    set_company_work_mode_default(
        db,
        company,
        C.WORK_MODE_AFTER_ONBOARDING,
        reason="guided_onboarding_completed",
    )
    return True


def resolve_project_work_mode(
    db: Session, company: Company, requested: ProjectWorkMode | None
) -> ProjectWorkMode:
    """项目创建时决定 `work_mode`（请求显式值优先 → 公司默认）。

    返回值会被**写入项目行**（快照，W35）。
    """
    if requested is not None:
        return requested
    return company_work_mode_default(db, company)


@dataclass(frozen=True)
class ExecutionPlanChoice:
    """立项时确定下来的执行策略（两个正交维度 + 可解释性）。

    写进项目行后就是**快照**（W35）：公司默认值之后怎么变都不改写它。
    """

    work_mode: ProjectWorkMode
    planning_fixture: PlanningFixture
    #: 是否因为"显式请求了 fixture"而把 work_mode 定为 managed（可解释性）
    fixture_forced_managed: bool = False

    @property
    def is_infrastructure_project(self) -> bool:
        return self.planning_fixture is PlanningFixture.deterministic_template


def resolve_execution_plan(
    db: Session,
    company: Company,
    requested_mode: ProjectWorkMode | None,
    requested_fixture: PlanningFixture | None,
    *,
    allow: bool | None = None,
) -> ExecutionPlanChoice:
    """立项时一次性解析 (work_mode, planning_fixture)，并保证两者不矛盾。

    规则（M2.1，D3/W33）：

    1. fixture 受部署门控；未开启时**422**，绝不静默降级；
    2. **fixture 隐含 `managed`**：确定性模板替掉的是 **Manager Agent 的规划**，
       而不是 guided 的"人类确认"环节 —— 所以"guided + fixture"是自相矛盾的组合。
       显式请求 fixture 时，`work_mode` 记为 `managed`（并把
       `fixture_forced_managed=True` 上报，让这件事可见而不是隐藏）；
    3. 无 fixture 时按正常优先级解析（请求值 → 公司默认）。
    """
    fixture = resolve_planning_fixture(requested_fixture, allow=allow)
    if fixture is PlanningFixture.deterministic_template:
        return ExecutionPlanChoice(
            work_mode=ProjectWorkMode.managed,
            planning_fixture=fixture,
            fixture_forced_managed=requested_mode is not ProjectWorkMode.managed,
        )
    return ExecutionPlanChoice(
        work_mode=resolve_project_work_mode(db, company, requested_mode),
        planning_fixture=fixture,
    )


def resolve_planning_fixture(
    requested: PlanningFixture | None, *, allow: bool | None = None
) -> PlanningFixture:
    """解析规划 fixture 请求（D3/B10，W33）。

    两道门，缺一不可：

    1. **显式请求**：调用方必须给出 `deterministic_template`；缺省/`none` 一律 `none`；
    2. **环境门控**：`Settings.allow_planning_fixtures` 必须为真（默认 False），
       否则 **422** —— 生产环境不会因为"顺手要了个 fixture"就跑起假规划。

    失败时抛 `WorkPolicyError`（由 API 层转成 422），**绝不静默降级**成 none
    —— 静默降级会让测试以为自己在测确定性链，实际测的是别的东西。
    """
    wanted = requested or PlanningFixture.none
    if wanted is PlanningFixture.none:
        return PlanningFixture.none
    permitted = settings.allow_planning_fixtures if allow is None else allow
    if not permitted:
        raise WorkPolicyError(
            "planning fixture is not enabled on this deployment "
            f"(set {C.PLANNING_FIXTURE_SETTING}=true; it is a test/tutorial/CI facility)"
        )
    return wanted
