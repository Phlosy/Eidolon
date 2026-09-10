"""教程门禁的唯一事实来源：只读真实业务状态。

为什么要单独一个模块：
- 步骤声明里的 ``requirement`` 必须真的被求值。旧实现里它只是个展示字符串，
  判定散在服务的 step-id if 链上 —— 加一步就得改服务，声明和实现还能悄悄不一致。
- 这里把它倒过来：``REQUIREMENTS[requirement_id]`` 才是判定，步骤只引用 id。
  新增步骤优先复用已有 requirement；确实要新门，就在这里加一条并配测试。

约束：
- 判定函数只读，绝不写库、绝不"顺手补数据"。
- 未知 requirement 在请求路径上按"未满足"处理（不能 500 把用户卡死），
  由 tests/test_tutorial_requirements.py 在开发期把拼写错误炸出来。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.logging import get_logger
from app.models.drive import DriveNode
from app.models.enums import (
    DriveNodeKind,
    EmployeeRole,
    LifecycleStatus,
    ProjectPhaseStatus,
    ResourceType,
    ReviewDecision,
    RuntimeInstanceStatus,
)
from app.models.lifecycle import EmployeePackage, ResourceAccount
from app.models.organization import Company, Employee
from app.models.project import Project
from app.models.provider import ModelBinding, Provider
from app.models.runtime import RuntimeInstance
from app.providers.base import PRESETS_BY_TYPE
from app.repositories import git as git_repo
from app.repositories import persons as person_repo
from app.repositories import project as project_repo
from app.repositories import project_delivery as delivery_repo
from app.services import position_compat

logger = get_logger(__name__)

# 运行到"能干活"的状态集合。mock runtime 没有容器，不能只认 running；
# 但 error/crashed/deleting 明确不算"配置完成"。
RUNTIME_BROKEN = {
    RuntimeInstanceStatus.error.value,
    RuntimeInstanceStatus.crashed.value,
    RuntimeInstanceStatus.deleting.value,
}
# 评审通过（含"有条件通过"，与交付流程口径一致）
PASSED_REVIEWS = {ReviewDecision.approved.value, ReviewDecision.conditionally_approved.value}
ACCOUNT_ACTIVE = "active"


def provider_usable(provider: Provider) -> bool:
    """“有效 Provider” = 启用 + 真能跑。

    有密钥的算；本地服务（Ollama）和自建 OpenAI 兼容服务本来就不需要密钥，
    只要有接入地址也算 —— 否则一个完全合法的配置会被教程判成未完成、
    而 UI 连密钥输入框都不给，用户就死锁在“接上模型服务”这一步。
    没密钥、又没有接入地址的自定义服务不算：跑起来必然失败。
    """
    if not provider.enabled:
        return False
    if provider.credential_ref:
        return True
    preset = PRESETS_BY_TYPE.get(provider.provider_type)
    if preset is None or preset.requires_api_key:
        return False
    return bool(provider.base_url or preset.default_base_url)


def _best_accounts(rows: list[ResourceAccount]) -> dict[int, dict[str, ResourceAccount]]:
    """每个员工每种资源保留"最可用"的一个账号：active > provisioning > pending > 其它。"""
    rank = {ACCOUNT_ACTIVE: 0, "provisioning": 1, "pending": 2}
    out: dict[int, dict[str, ResourceAccount]] = {}
    for row in rows:
        current = out.setdefault(row.employee_id, {}).get(row.resource_type)
        if current is None or rank.get(row.status, 9) < rank.get(current.status, 9):
            out[row.employee_id][row.resource_type] = row
    return out


@dataclass(slots=True)
class Facts:
    """一次进度评估所需的全部域数据；GET progress 只读一轮。"""

    company: Company | None = None
    employees: list[Employee] = field(default_factory=list)
    runtimes: dict[int, RuntimeInstance] = field(default_factory=dict)
    bindings: dict[int, list[ModelBinding]] = field(default_factory=dict)
    usable_providers: set[int] = field(default_factory=set)
    # provider id -> Provider：门禁只看 usable 集合，展示层（实战成本说明）需要名字
    providers: dict[int, Provider] = field(default_factory=dict)
    accounts: dict[int, dict[str, ResourceAccount]] = field(default_factory=dict)
    packaged_employees: set[int] = field(default_factory=set)
    has_document: bool = False
    git_enabled: bool = False
    project: Project | None = None
    reviews: dict[str, str] = field(default_factory=dict)
    phases: dict[str, str] = field(default_factory=dict)
    delivery_count: int = 0
    # 旧 role 口径的**派生**镜像（员工 id -> role）。`load()` 一次算完，之后所有 role
    # 判断都读它，不再读 `employee.role` 列（P4c）。
    # `None` 表示"没算过"，**不是**"没人有 role"：手工构造 Facts 而不给这个字段，
    # 一碰 role 门禁就会抛错，而不是静默把所有门禁判成 False（ADR-10）。
    role_of: dict[int, str] | None = None

    @classmethod
    def load(cls, db: Session, company: Company | None, project_id: int | None = None) -> Facts:
        facts = cls(company=company)
        if company is None:
            return facts

        facts.employees = list(
            db.scalars(
                select(Employee).where(Employee.company_id == company.id).order_by(Employee.id)
            )
        )
        facts.role_of = position_compat.role_mirror(db, facts.employees)
        ids = [employee.id for employee in facts.employees]
        if ids:
            # R1.4：运行时实例/模型绑定的属主口径切 person_id（批量解析 + 回落集）
            person_ids = person_repo.resolve_person_ids(db, ids)
            fallback_ids = [emp_id for emp_id in ids if emp_id not in person_ids]

            def _owner_clause(person_column, employee_column):
                clause = person_column.in_(person_ids.values())
                if fallback_ids:
                    clause = or_(clause, employee_column.in_(fallback_ids))
                return clause

            for runtime in db.scalars(
                select(RuntimeInstance).where(
                    _owner_clause(RuntimeInstance.person_id, RuntimeInstance.employee_id)
                )
            ):
                facts.runtimes[runtime.employee_id] = runtime
            bindings = list(
                db.scalars(
                    select(ModelBinding).where(
                        _owner_clause(ModelBinding.person_id, ModelBinding.employee_id)
                    )
                )
            )
            for binding in bindings:
                facts.bindings.setdefault(binding.employee_id, []).append(binding)
            # "有效 Provider Binding" = 绑定存在 + Provider 启用 + 真的存了密钥。
            # 没有密钥的 Provider 不能算完成：员工一跑就会失败，教程却显示通关。
            provider_ids = {binding.provider_id for binding in bindings}
            if provider_ids:
                for provider in db.scalars(select(Provider).where(Provider.id.in_(provider_ids))):
                    facts.providers[provider.id] = provider
                    # “有效 Provider Binding” = 绑定存在 + Provider 启用 + 真能跑。
                    # 没有密钥的云端 Provider 不能算完成；但 Ollama 这类本地服务
                    # 本来就不需要密钥，不能因此把它判成未配置（否则教程死锁）。
                    if provider_usable(provider):
                        facts.usable_providers.add(provider.id)
            facts.accounts = _best_accounts(
                list(
                    db.scalars(select(ResourceAccount).where(ResourceAccount.employee_id.in_(ids)))
                )
            )
            facts.packaged_employees = set(
                db.scalars(
                    select(EmployeePackage.employee_id).where(EmployeePackage.employee_id.in_(ids))
                )
            )
        facts.has_document = (
            db.scalar(
                select(DriveNode.id)
                .where(
                    DriveNode.company_id == company.id,
                    DriveNode.kind == DriveNodeKind.document.value,
                )
                .limit(1)
            )
            is not None
        )
        facts.git_enabled = any(connection.enabled for connection in git_repo.list_connections(db))
        if project_id is not None:
            project = project_repo.get_project(db, project_id)
            if project is not None and project.company_id == company.id:
                facts.project = project
                for review in delivery_repo.list_reviews(db, project.id):
                    facts.reviews[review.review_type] = review.decision
                for phase in delivery_repo.list_phases(db, project.id):
                    facts.phases[phase.phase_type] = phase.status
                facts.delivery_count = len(delivery_repo.list_delivery_packages(db, project.id))
        return facts

    # ---- 角色查询：教程认"在岗或正在入职"的那个人 ----
    # 为什么不是严格 active：入职任务里有 git 等资源是**可选**的（核心教程自己的
    # git_setup 就是 OPTIONAL_ACTION）。开发环境里 builtin gitea 未安装时，
    # git 步骤全部 failed → onboarding job 停在 partial → lifecycle_status 永远是
    # onboarding → 教程第 2 步永久死锁（实测：员工 19 就是这样卡住的）。
    # 因此角色定位放宽到 {onboarding, active}，但仍然要求真实的 runtime +
    # workspace provisioning 发生过（见 onboarded 档），点"下一步"造不出来。
    IN_POST = (LifecycleStatus.active.value, LifecycleStatus.onboarding.value)

    def role(self, employee: Employee) -> str | None:
        """某个人的旧 role 口径（派生镜像的唯一读法）。"""
        if self.role_of is None:
            raise RuntimeError(
                "Facts.role_of 未计算：请用 Facts.load(db, ...)，或手工传 role_of=。"
                "缺了它不让 role 门禁静默全 False —— 那是假阴性，比报错更难查。"
            )
        return self.role_of.get(getattr(employee, "id", None))

    def in_post_of(self, role: str) -> Employee | None:
        match = None
        for employee in self.employees:
            if self.role(employee) != role or employee.lifecycle_status not in self.IN_POST:
                continue
            # 同时存在时优先真正 active 的那位
            if match is None or (
                employee.lifecycle_status == LifecycleStatus.active.value
                and match.lifecycle_status != LifecycleStatus.active.value
            ):
                match = employee
        return None if match is None else match

    def runtime_of(self, employee: Employee | None) -> RuntimeInstance | None:
        return self.runtimes.get(employee.id) if employee else None

    def provider_of(self, binding: ModelBinding | None) -> Provider | None:
        return self.providers.get(binding.provider_id) if binding else None

    def account(self, employee: Employee | None, resource_type: str) -> ResourceAccount | None:
        return self.accounts.get(employee.id, {}).get(resource_type) if employee else None


def _runtime_configured(facts: Facts, role: str) -> bool:
    runtime = facts.runtime_of(facts.in_post_of(role))
    return runtime is not None and runtime.status not in RUNTIME_BROKEN


def _provider_configured(facts: Facts, role: str) -> bool:
    employee = facts.in_post_of(role)
    if employee is None:
        return False
    return any(
        binding.provider_id in facts.usable_providers
        for binding in facts.bindings.get(employee.id, [])
    )


def _workspace_provisioned(facts: Facts, role: str) -> bool:
    account = facts.account(facts.in_post_of(role), ResourceType.workspace.value)
    return account is not None and account.status == ACCOUNT_ACTIVE


def _access_provisioned(facts: Facts, role: str) -> bool:
    """权限包已分配，且云文档资源账号真的建好。

    不看 docs 账号的话，"分配了权限包"和"权限真的落到系统里"会被混为一谈。
    """
    employee = facts.in_post_of(role)
    if employee is None:
        return False
    docs = facts.account(employee, ResourceType.docs.value)
    return (
        employee.id in facts.packaged_employees
        and docs is not None
        and docs.status == ACCOUNT_ACTIVE
    )


def _role_gate(role: str, part: str) -> Callable[[Facts], bool]:
    """(角色, 环节) 组装判定，避免每个角色抄一遍。"""
    if part == "active":
        # 严格档：只有 lifecycle_status == active 才算。核心教程用它会被可选资源
        # 卡死，所以留给"确实需要活着的运行时"的场景。
        return lambda facts: any(
            facts.role(employee) == role
            and employee.lifecycle_status == LifecycleStatus.active.value
            for employee in facts.employees
        )
    if part == "onboarded":
        # 教程用这档：员工真的被创建、runtime 与 workspace 真的被开出 =
        # 用户确实走完了真实入职向导；git 之类可选资源失败不再阻塞教程。
        return lambda facts: (
            _runtime_configured(facts, role) and _workspace_provisioned(facts, role)
        )
    if part == "runtime":
        return lambda facts: _runtime_configured(facts, role)
    if part == "provider":
        return lambda facts: _provider_configured(facts, role)
    if part == "workspace":
        return lambda facts: _workspace_provisioned(facts, role)
    if part == "access":
        return lambda facts: _access_provisioned(facts, role)
    if part == "resources":
        return lambda facts: (
            _workspace_provisioned(facts, role) and _access_provisioned(facts, role)
        )
    if part == "ready":
        checks = (
            lambda facts: _runtime_configured(facts, role),
            lambda facts: _provider_configured(facts, role),
            lambda facts: _workspace_provisioned(facts, role),
            lambda facts: _access_provisioned(facts, role),
        )
        return lambda facts: all(check(facts) for check in checks)
    raise ValueError(f"unknown gate part: {part}")


def _review_approved(kind: str) -> Callable[[Facts], bool]:
    return lambda facts: facts.project is not None and facts.reviews.get(kind) in PASSED_REVIEWS


def _phase_completed(*keys: str) -> Callable[[Facts], bool]:
    return lambda facts: (
        facts.project is not None
        and all(facts.phases.get(key) == ProjectPhaseStatus.completed.value for key in keys)
    )


REQUIREMENTS: dict[str, Callable[[Facts], bool]] = {
    "COMPANY_CREATED": lambda facts: facts.company is not None,
    "EMPLOYEE_CREATED": lambda facts: len(facts.employees) > 0,
    # ---- CEO：入职 → Runtime → Provider → Workspace → 权限 ----
    "CEO_ACTIVE": _role_gate(EmployeeRole.ceo.value, "active"),
    "CEO_ONBOARDED": _role_gate(EmployeeRole.ceo.value, "onboarded"),
    "CEO_RUNTIME_CONFIGURED": _role_gate(EmployeeRole.ceo.value, "runtime"),
    "CEO_PROVIDER_CONFIGURED": _role_gate(EmployeeRole.ceo.value, "provider"),
    "CEO_WORKSPACE_PROVISIONED": _role_gate(EmployeeRole.ceo.value, "workspace"),
    "CEO_ACCESS_PROVISIONED": _role_gate(EmployeeRole.ceo.value, "access"),
    # 核心教程把"workspace + 权限"合成一步：它们来自同一次 provisioning，
    # 拆成两步只会在向导结束后连闪两个对话框。
    "CEO_RESOURCES_PROVISIONED": _role_gate(EmployeeRole.ceo.value, "resources"),
    # ---- 公司系统 ----
    "COMPANY_DOCUMENT_CREATED": lambda facts: facts.has_document,
    "GIT_CONFIGURED": lambda facts: facts.git_enabled,
    # ---- Engineer ----
    "ENGINEER_ACTIVE": _role_gate(EmployeeRole.engineer.value, "active"),
    "ENGINEER_ONBOARDED": _role_gate(EmployeeRole.engineer.value, "onboarded"),
    "ENGINEER_RUNTIME_CONFIGURED": _role_gate(EmployeeRole.engineer.value, "runtime"),
    "ENGINEER_PROVIDER_CONFIGURED": _role_gate(EmployeeRole.engineer.value, "provider"),
    "ENGINEER_WORKSPACE_PROVISIONED": _role_gate(EmployeeRole.engineer.value, "workspace"),
    "ENGINEER_ACCESS_PROVISIONED": _role_gate(EmployeeRole.engineer.value, "access"),
    "ENGINEER_READY": _role_gate(EmployeeRole.engineer.value, "ready"),
    # ---- QA ----
    "QA_ACTIVE": _role_gate(EmployeeRole.qa_engineer.value, "active"),
    # ---- 实战项目（Classic Snake practice）----
    "FIRST_PROJECT_CREATED": lambda facts: facts.project is not None,
    "REQUIREMENTS_APPROVED": _review_approved("requirements_review"),
    "DESIGN_APPROVED": _review_approved("design_review"),
    "ACCEPTANCE_APPROVED": _review_approved("acceptance_review"),
    "DEVELOPMENT_COMPLETED": _phase_completed("development"),
    "TESTING_COMPLETED": _phase_completed("internal_testing", "user_acceptance_testing"),
    "DELIVERY_COMPLETED": lambda facts: (
        facts.project is not None
        and facts.project.status == "completed"
        and facts.delivery_count > 0
    ),
}

# 依赖具体项目的门：推进到这些步骤前必须先把 project_id 记进 context
_PROJECT_SCOPED = {
    "FIRST_PROJECT_CREATED",
    "REQUIREMENTS_APPROVED",
    "DESIGN_APPROVED",
    "ACCEPTANCE_APPROVED",
    "DEVELOPMENT_COMPLETED",
    "TESTING_COMPLETED",
    "DELIVERY_COMPLETED",
}


def known() -> set[str]:
    """所有已注册的 requirement id（给声明校验和测试用）。"""
    return set(REQUIREMENTS)


def evaluate(requirement: str, facts: Facts | None) -> bool:
    """按 requirement id 求值；任何异常都只得出"未满足"。

    GET /tutorial 每次打开都会跑一遍所有门。这里抛出去就是 500 + 教程彻底不可用，
    而"门没过"顶多是进度不前进，并且会被记进日志。
    """
    check = REQUIREMENTS.get(requirement)
    if check is None or facts is None:
        if check is None:
            logger.warning("unknown tutorial requirement: %s", requirement)
        return False
    # 派生输入没算过是**编程错误**，不是"门没过"：放在 try 外面，免得被下面那个
    # 兜底 except 吞成 False（那会让人以为门禁逻辑坏了，而不是 Facts 少填了一个字段）。
    if facts.employees and facts.role_of is None:
        raise RuntimeError("Facts.role_of 未计算：用 Facts.load(db, ...) 构造，或显式传 role_of=。")
    try:
        return bool(check(facts))
    except Exception:  # noqa: BLE001 - 一个坏门不能把整个教程打挂
        logger.exception("tutorial requirement evaluation failed: %s", requirement)
        return False


def project_scoped(requirement: str) -> bool:
    return requirement in _PROJECT_SCOPED
