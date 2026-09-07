"""人才名册 v2 查询服务（P9，docs/talent-roster.md）。

把 Employee / WorkforceStatus / CurrentPosition / Runtime / Provider / Traits /
Competencies / Assessment / Activity 聚合成统一人才视图 —— 全部复用既有事实与派生
出口（WorkforceStatusResolver / position_repo / BrainTraits / EmployeeCompetency），
**不建第二真相**。

批量纪律：一页内所有人才共走 ~9 条一次性查询（人 / 状态视图 / 主职 / 完整性 /
runtime / 绑定+provider / brain / competencies / 最近事件），不逐人 SQL（有守卫）。
默认排除历史/离职（offboarded、pending）—— 不混入默认列表；`status` 可显式带它。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.brain.traits import BrainTraits
from app.models.competency import CompetencyDefinition, CompetencyDomain, EmployeeCompetency
from app.models.event import Event
from app.models.organization import Department, Employee
from app.models.provider import ModelBinding, Provider
from app.models.runtime import EmployeeBrain, RuntimeInstance
from app.repositories import organization as org_repo
from app.repositories import position as position_repo
from app.services import position_service
from app.workforce.status import WorkforceStatusResolver

#: 通用能力目录 general 维度数（assessment coverage 口径基数）
GENERAL_DIMENSION_COUNT = 10

#: 默认排除的历史离职态（P9 §2：不把已离职员工混入默认列表；pending 保留，旧测试全量口径
#: 仍以 include_offboarded=true 显式纳入历史）
_EXCLUDED_LIFECYCLE = {"offboarded"}


class _Derived:
    """一名人才的批量派生视图（纯内存聚合，避免逐人 N+1）。"""

    def __init__(self, person: Employee) -> None:
        self.employee_id = int(person.id)
        self.lifecycle_status: str = person.lifecycle_status
        self.runtime_type: str | None = None
        self.runtime_status: str | None = None
        self.provider_id: int | None = None
        self.provider_name: str | None = None
        self.provider_model: str | None = None
        self.traits: dict[str, float] = {}
        self.trait_labels: list[str] = []
        self.competency_scores: dict[str, dict] = {}
        self.last_event_type: str | None = None
        self.last_event_at = None
        self.current_position = None
        self.workforce_status = ""
        self.has_primary_assignment = False
        self.occupies_establishment = False
        self.integrity: list[str] = []


def roster_query(
    db: Session,
    company_id: int | None,
    *,
    statuses: list[str] | None = None,
    department_id: int | None = None,
    position_code: str | None = None,
    runtime_type: str | None = None,
    provider_id: int | None = None,
    competency_code: str | None = None,
    min_competency_score: float | None = None,
    min_competency_confidence: float | None = None,
    trait_code: str | None = None,
    min_trait_value: float | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    include_offboarded: bool = False,
) -> dict:
    people = org_repo.list_employees(db, company_id)
    party, department_names = _batch_derived(db, people)

    def _matches(person: Employee) -> bool:
        if not include_offboarded and person.lifecycle_status in _EXCLUDED_LIFECYCLE:
            return False
        d = party[int(person.id)]
        if statuses and d.workforce_status not in statuses:
            return False
        if department_id is not None and person.department_id != department_id:
            return False
        current = d.current_position
        if position_code == "none":
            if current is not None:
                return False
        elif position_code and (current or {}).get("position_code") != position_code:
            return False
        if runtime_type and d.runtime_type != runtime_type:
            return False
        if provider_id and d.provider_id != provider_id:
            return False
        if competency_code is not None:
            item = d.competency_scores.get(competency_code)
            if item is None or item["score"] is None:
                return False
            if min_competency_score is not None and item["score"] < min_competency_score:
                return False
            if (
                min_competency_confidence is not None
                and (item["confidence"] or 0) < min_competency_confidence
            ):
                return False
        if trait_code is not None:
            value = d.traits.get(trait_code)
            if value is None or value < (min_trait_value or 0):
                return False
        query = (search or "").strip().lower()
        if query and query not in f"{person.name} {person.slug}".lower():
            return False
        return True

    matched = [person for person in people if _matches(person)]
    matched.sort(key=lambda person: (person.name.lower(), int(person.id)))
    total = len(matched)
    page = matched[offset : offset + limit]
    items = [_entry(person, party[int(person.id)], department_names) for person in page]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _batch_derived(db: Session, people: list[Employee]):
    derived = {int(person.id): _Derived(person) for person in people}
    ids = list(derived)

    resolver = WorkforceStatusResolver(db)
    views = resolver.views(people)
    for person in people:
        view = views[int(person.id)]
        d = derived[int(person.id)]
        d.workforce_status = view.workforce_status.value
        d.has_primary_assignment = view.has_primary_assignment
        d.occupies_establishment = view.occupies_establishment

    positions = position_repo.current_positions_by_employee(db, ids)
    for employee_id, position in positions.items():
        derived[employee_id].current_position = _current_payload(position)

    issues = position_service.integrity_by_employee(db)
    for employee_id, kinds in issues.items():
        derived[employee_id].integrity = kinds

    runtimes = {
        runtime.employee_id: runtime
        for runtime in db.scalars(
            select(RuntimeInstance).where(RuntimeInstance.employee_id.in_(ids))
        )
    }
    bindings = list(db.scalars(select(ModelBinding).where(ModelBinding.employee_id.in_(ids))))
    provider_ids = {binding.provider_id for binding in bindings if binding.provider_id}
    providers = {
        provider.id: provider
        for provider in db.scalars(select(Provider).where(Provider.id.in_(provider_ids)))
    }
    for employee_id, runtime in runtimes.items():
        d = derived[employee_id]
        d.runtime_type = runtime.runtime_type
        d.runtime_status = runtime.status
    for binding in bindings:
        d = derived[binding.employee_id]
        provider = providers.get(binding.provider_id) if binding.provider_id else None
        d.provider_id = binding.provider_id
        d.provider_name = provider.name if provider else None
        d.provider_model = binding.model or (provider.name if provider else None)

    brains = {
        brain.employee_id: brain
        for brain in db.scalars(select(EmployeeBrain).where(EmployeeBrain.employee_id.in_(ids)))
    }
    for employee_id, brain in brains.items():
        traits = BrainTraits.from_brain(brain)
        values = {code: float(traits[code]) for code in list(traits)}
        derived[employee_id].traits = values
        top = sorted(
            ((code, value) for code, value in values.items() if value > 0.65),
            key=lambda item: item[1],
            reverse=True,
        )[:3]
        derived[employee_id].trait_labels = [code for code, _value in top]

    definitions = _definition_map(db)
    domain_kinds = _domain_kinds(db)
    for row in db.scalars(
        select(EmployeeCompetency).where(EmployeeCompetency.employee_id.in_(ids))
    ).all():
        definition = definitions.get(row.competency_definition_id)
        if definition is None:
            continue
        derived[row.employee_id].competency_scores[definition.code] = {
            "score": row.score,
            "confidence": row.confidence,
            "status": row.status,
            "kind": domain_kinds.get(definition.domain_id, "general"),
        }

    latest_ids = db.scalars(
        select(func.max(Event.id))
        .where(Event.actor_employee_id.in_(ids))
        .group_by(Event.actor_employee_id)
    ).all()
    if latest_ids:
        for row in db.execute(
            select(Event.actor_employee_id, Event.type, Event.created_at).where(
                Event.id.in_(list(latest_ids))
            )
        ).all():
            if row[0] in derived:
                derived[int(row[0])].last_event_type = row[1]
                derived[int(row[0])].last_event_at = row[2]

    department_ids = {person.department_id for person in people if person.department_id}
    department_names = {
        department.id: department.name
        for department in db.scalars(
            select(Department).where(Department.id.in_(list(department_ids)))
        )
    }
    return derived, department_names


def _current_payload(position) -> dict | None:
    if position is None:
        return None
    return {
        "definition_id": position.definition_id,
        "code": position.code,
        "name": position.name,
        "level": position.level,
        "job_family": position.job_family,
        "department_id": position.department_id,
        "department_name": position.department_name,
        "slot_id": position.slot_id,
        "slot_code": position.slot_code,
        "since": position.since,
        "assignment_type": position.assignment_type,
        "position_is_custom": position.is_custom,
    }


def _entry(person: Employee, d: _Derived, department_names: dict[int, str]) -> dict:
    assessed = sum(
        1
        for item in d.competency_scores.values()
        if item["score"] is not None and (item["confidence"] or 0) >= 0.4
    )
    return {
        "employee_id": d.employee_id,
        "name": person.name,
        "slug": person.slug,
        "avatar": person.avatar or "",
        "department_id": person.department_id,
        "department_name": (
            department_names.get(person.department_id) if person.department_id else None
        ),
        "lifecycle_status": d.lifecycle_status,
        "workforce_status": d.workforce_status,
        "has_primary_assignment": d.has_primary_assignment,
        "occupies_establishment": d.occupies_establishment,
        "current_position": d.current_position,
        "integrity": d.integrity,
        "runtime": (
            {"type": d.runtime_type, "status": d.runtime_status} if d.runtime_type else None
        ),
        "provider": (
            {"provider_id": d.provider_id, "name": d.provider_name, "model": d.provider_model}
            if d.provider_id
            else None
        ),
        "traits_summary": [{"code": code, "value": d.traits[code]} for code in d.trait_labels],
        "top_general_competencies": _top_competencies(d, "general", 2),
        "top_professional_competencies": _top_competencies(d, "professional", 3),
        "assessment_summary": {
            "assessed_general_count": sum(
                1
                for item in d.competency_scores.values()
                if item["kind"] == "general"
                and item["score"] is not None
                and (item["confidence"] or 0) >= 0.4
            ),
            "general_total": GENERAL_DIMENSION_COUNT,
            "evidence_coverage": _coverage_label(assessed),
        },
        "recent_activity": (
            {"type": d.last_event_type, "at": d.last_event_at} if d.last_event_type else None
        ),
    }


def _top_competencies(d: _Derived, kind: str, count: int) -> list[dict]:
    """Top 能力：只挑 已评估 且 confidence ≥ 0.4（低置信只能当 Promising，不进 Top）。"""
    candidates = [
        (code, item)
        for code, item in d.competency_scores.items()
        if item["kind"] == kind and item["score"] is not None and (item["confidence"] or 0) >= 0.4
    ]
    candidates.sort(key=lambda pair: pair[1]["score"] or 0, reverse=True)
    return [
        {"code": code, "score": item["score"], "confidence": item["confidence"]}
        for code, item in candidates[:count]
    ]


def _coverage_label(assessed: int) -> str:
    if assessed == 0:
        return "none"
    if assessed < 4:
        return "low"
    if assessed < 8:
        return "medium"
    return "high"


def _definition_map(db: Session) -> dict[int, CompetencyDefinition]:
    return {definition.id: definition for definition in db.scalars(select(CompetencyDefinition))}


def _domain_kinds(db: Session) -> dict[int, str]:
    return {domain.id: domain.kind for domain in db.scalars(select(CompetencyDomain))}
