"""Access packages & entitlement algebra (v0.4 §5/§6/§9).

Desired entitlements = union over the employee's AccessPackages, deduped by
entitlement id. Transfer computes ADD/REMOVE/KEEP between the old and new
package sets; role-derived packages (source="role") are managed automatically,
manual ones are never removed implicitly.
"""

import logging
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.enums import PackageSource
from app.models.lifecycle import AccessPackage, EmployeePackage, Entitlement
from app.models.position import PositionAssignment
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import position as position_repo

logger = logging.getLogger("eidolon.access")

BASE_PACKAGE_SLUG = "base-employee"

ROLE_TO_PACKAGE_SLUG = {
    "ceo": "ceo",
    "product_manager": "product-manager",
    "researcher": "researcher",
    "engineer": "engineer",
    "qa_engineer": "qa-engineer",
}

# department slug -> package slug (seed departments map 1:1 to roles)
DEPARTMENT_TO_PACKAGE_SLUG = {
    "executive": "ceo",
    "product": "product-manager",
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa-engineer",
}

DEPARTMENT_TO_ROLE = {
    "executive": "ceo",
    "product": "product_manager",
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa_engineer",
}


# source 值 → 层级。`position` 是职位层；role/manual/legacy 都归人级
# （人级 = 跟着这个人走的权限，离职才回收，调岗绝不动）。
PERSON_SOURCES = {
    PackageSource.manual.value,
    PackageSource.role.value,
    PackageSource.project.value,
}


def layer_of_source(source: str | None) -> str:
    """权限来源 → 层级（`person` / `position`）。唯一口径，别处不许自己判。"""
    return "position" if source == PackageSource.position.value else "person"


@dataclass
class EffectiveEntitlement:
    entitlement: Entitlement
    sources: list[dict] = field(default_factory=list)  # [{package_id, package_name, source, layer}]


@dataclass
class AccessDiff:
    add: list[Entitlement] = field(default_factory=list)
    remove: list[Entitlement] = field(default_factory=list)
    keep: list[Entitlement] = field(default_factory=list)


def package_entitlements(db: Session, package: AccessPackage) -> list[Entitlement]:
    items = lifecycle_repo.list_package_items(db, package.id)
    return [
        entitlement
        for item in items
        if (entitlement := lifecycle_repo.get_entitlement(db, item.entitlement_id)) is not None
    ]


def union_entitlements(
    db: Session,
    packages: list[AccessPackage],
    *,
    layer_by_package: dict[int, str] | None = None,
) -> list[EffectiveEntitlement]:
    """Union over packages, deduped by entitlement id, tracking source packages.

    `layer_by_package`（package_id → `person`/`position`）让"这条权限是谁给的"可追溯：
    同一个 entitlement 可能同时来自人级 base 包与职位包，`sources` 会列出两条。
    不给就按 `person` 处理 —— 职位层是唯一需要显式声明的例外。
    """
    merged: dict[int, EffectiveEntitlement] = {}
    for package in packages:
        layer = (layer_by_package or {}).get(package.id, "person")
        for entitlement in package_entitlements(db, package):
            entry = merged.setdefault(entitlement.id, EffectiveEntitlement(entitlement=entitlement))
            entry.sources.append(
                {
                    "package_id": package.id,
                    "package_name": package.name,
                    "layer": layer,
                }
            )
    return list(merged.values())


def employee_packages(db: Session, employee_id: int) -> list[tuple[EmployeePackage, AccessPackage]]:
    """(授予行, 包) —— 保留 `source`，否则层级信息在第一步就丢了。"""
    out: list[tuple[EmployeePackage, AccessPackage]] = []
    for row in lifecycle_repo.list_employee_packages(db, employee_id):
        package = lifecycle_repo.get_package(db, row.package_id)
        if package is not None:
            out.append((row, package))
    return out


def employee_entitlements(db: Session, employee_id: int) -> list[EffectiveEntitlement]:
    pairs = employee_packages(db, employee_id)
    layers = {package.id: layer_of_source(row.source) for row, package in pairs}
    return union_entitlements(db, [package for _, package in pairs], layer_by_package=layers)


def effective_access(db: Session, employee_id: int) -> dict[str, list[EffectiveEntitlement]]:
    """**Effective Access = 人级 + 职位级**（docs/position-system.md §4）。

    返回三层视图：
      · `person`   —— 跟着人走的（base-employee、人级角色包、手动授予、项目包）
      · `position` —— 因为占着某个编制才有的
      · `effective`—— 两者并集（按 entitlement 去重）

    为什么把两层都算出来而不是只给并集：调岗页与诊断要回答
    "这条权限是我本来就有的，还是当上这个职位才有的"，只给并集就答不出来。
    """
    pairs = employee_packages(db, employee_id)
    person = [package for row, package in pairs if layer_of_source(row.source) == "person"]
    position = [package for row, package in pairs if layer_of_source(row.source) == "position"]
    layers = {row.package_id: layer_of_source(row.source) for row, package in pairs}
    return {
        "person": union_entitlements(db, person, layer_by_package=layers),
        "position": union_entitlements(db, position, layer_by_package=layers),
        "effective": union_entitlements(
            db, [package for _, package in pairs], layer_by_package=layers
        ),
    }


# ------------------------------------------------------------- 职位层（P4d）


def position_assignments(db: Session, employee_id: int) -> list[PositionAssignment]:
    """还在生效的任职（主职 / 代理 / 兼任 / 临时都算）。

    职位权限跟着**实际职责**走：只认 PRIMARY 会让人代理一次就得手动加包。
    这里刻意**不**复用占用态那把尺 —— "占不占编制"只数 PRIMARY（ADR-2），
    "有没有这个职位的权限"数所有生效任职，两个问题不同，答案不必相同。
    关窗的行（`effective_to` 非空）由 `active_assignments()` 排除。
    """
    return position_repo.active_assignments(db, employee_id)


def position_packages_for(db: Session, employee_id: int) -> list[AccessPackage]:
    """当前任职解析出的职位包（定义 → `position_definition_packages`）。

    解析链任一环断掉（无坑、无定义、包被删）就**跳过那一环**而不是猜一个包；
    职位层为空是合法状态（多数定义还没声明默认权限），此时人级权限原样保留。
    """
    assignments = position_assignments(db, employee_id)
    definition_ids: list[int] = []
    for assignment in assignments:
        if assignment.position_slot_id is None:
            continue
        slot = position_repo.get_slot(db, int(assignment.position_slot_id))
        if slot is None:
            continue
        definition_ids.append(int(slot.position_definition_id))
    if not definition_ids:
        return []
    slug_map = position_repo.definition_packages_map(db, definition_ids)
    slugs: list[str] = []
    for definition_id in definition_ids:
        for slug in slug_map.get(definition_id, []):
            if slug not in slugs:
                slugs.append(slug)
    packages: list[AccessPackage] = []
    for slug in slugs:
        package = lifecycle_repo.get_package_by_slug(db, slug)
        if package is None:
            # 定义声明了一个不存在的包：不编造，也不因此撤掉别的包（诊断另有出口）
            continue
        packages.append(package)
    return packages


def sync_position_packages(
    db: Session,
    employee_id: int,
    desired: list[AccessPackage],
    *,
    person_claimed_ids: set[int] | None = None,
) -> tuple[list[int], list[int]]:
    """把 `source="position"` 的行收敛到期望集；只碰职位层。

    与 `sync_role_packages()` 的三点区别（都是"两层不能互相拆台"的直接后果）：

    1. 删除时只删 `source=position` 的行；
    2. **已经被人级（role/manual/project）声称过的包不删** —— 职位结束不等于此人
       不该再有这条权限（`person_claimed_ids` 就是把这道锁交给调用方明确传进来，
       而不是在函数里偷偷回查一遍现状）；
    3. 新增时如果同 id 的包已经有别的层的行，就**不再造第二行**：并集按 entitlement
       去重，多一行只是把同一份权限授予两次。
    """
    protected = person_claimed_ids or set()
    desired_ids = {package.id for package in desired}
    rows = lifecycle_repo.list_employee_packages(db, employee_id)
    existing_ids = {row.package_id for row in rows}
    added: list[int] = []
    removed: list[int] = []
    for row in rows:
        if (
            row.source == PackageSource.position.value
            and row.package_id not in desired_ids
            and row.package_id not in protected
        ):
            lifecycle_repo.delete_employee_package(db, row)
            removed.append(row.package_id)
    for package in desired:
        if package.id not in existing_ids:
            lifecycle_repo.create_employee_package(
                db,
                employee_id=employee_id,
                package_id=package.id,
                source=PackageSource.position.value,
            )
            added.append(package.id)
    db.flush()
    return added, removed


def resolve_packages(
    db: Session,
    *,
    role: str | None = None,
    department_slug: str | None = None,
    extra_package_ids: list[int] | None = None,
) -> list[AccessPackage]:
    """base-employee + the role/department-mapped package + explicit extras."""
    packages: list[AccessPackage] = []
    seen: set[int] = set()

    def _add(package: AccessPackage | None) -> None:
        if package is not None and package.id not in seen:
            seen.add(package.id)
            packages.append(package)

    _add(lifecycle_repo.get_package_by_slug(db, BASE_PACKAGE_SLUG))
    slug = None
    if department_slug is not None:
        slug = DEPARTMENT_TO_PACKAGE_SLUG.get(department_slug)
    if slug is None and role is not None:
        slug = ROLE_TO_PACKAGE_SLUG.get(role)
    if slug is not None and slug != BASE_PACKAGE_SLUG:
        _add(lifecycle_repo.get_package_by_slug(db, slug))
    for package_id in extra_package_ids or []:
        _add(lifecycle_repo.get_package(db, package_id))
    return packages


def sync_role_packages(
    db: Session, employee_id: int, desired: list[AccessPackage]
) -> tuple[list[int], list[int]]:
    """Reconcile source="role" assignments with the desired set; manual rows are
    left untouched.

    返回 `(added, removed)` —— 但内部还会把职位层的行**认领**成人级行（见下面的
    人级优先注释）。认领不新增/移除权限，所以不体现在返回值里；要审计它请看
    `effective_access()` 的层级。职位层请用 `sync_position_packages()`。
    """
    desired_ids = {p.id for p in desired}
    added: list[int] = []
    removed: list[int] = []
    adopted: list[int] = []
    existing = lifecycle_repo.list_employee_packages(db, employee_id)
    # 一次取全再在内存里判存在，别为每个期望包补一条查询（也避免与上面的删除循环看到不一致的快照）
    existing_row_by_package = {row.package_id: row for row in existing}
    for row in existing:
        if row.source == PackageSource.role.value and row.package_id not in desired_ids:
            lifecycle_repo.delete_employee_package(db, row)
            removed.append(row.package_id)
    for package in desired:
        source = (
            PackageSource.manual.value
            if package.slug not in (BASE_PACKAGE_SLUG, *ROLE_TO_PACKAGE_SLUG.values())
            else PackageSource.role.value
        )
        existing_row = existing_row_by_package.get(package.id)
        if existing_row is None:
            lifecycle_repo.create_employee_package(
                db, employee_id=employee_id, package_id=package.id, source=source
            )
            added.append(package.id)
        elif existing_row.source == PackageSource.position.value:
            # **人级优先**：这个包同时由职位和人级声称，所有权归人级。
            # 不认领的话，离开职位那天职位层会把行删掉 —— 人级本该保留的访问被误撤。
            existing_row.source = source
            adopted.append(package.id)
    if adopted:
        # 认领只改 provenance，不改权限集合 —— 但它是"谁给了这条权限"的答案变更，
        # 不留痕迹的话，职位层日后为何没撤这条就查不出来了。
        logger.info("employee %s: %d 个职位包被人级认领 %s", employee_id, len(adopted), adopted)
    db.flush()
    return added, removed


def diff_entitlements(
    current: list[EffectiveEntitlement], desired: list[EffectiveEntitlement]
) -> AccessDiff:
    """§6: ADD = desired - current; REMOVE = current - desired; KEEP = ∩."""
    current_by_id = {e.entitlement.id: e.entitlement for e in current}
    desired_by_id = {e.entitlement.id: e.entitlement for e in desired}
    return AccessDiff(
        add=[e for key, e in desired_by_id.items() if key not in current_by_id],
        remove=[e for key, e in current_by_id.items() if key not in desired_by_id],
        keep=[e for key, e in desired_by_id.items() if key in current_by_id],
    )
