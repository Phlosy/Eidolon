"""Narrative reports (R7-b): the facts, folded into a story a person can read.

What was asked, what was done, who decided what and where, what it cost, where it is
stuck, and what is needed from you next. Every line carries the ids of the facts it was
made from, so a reader can drill down to the record behind any sentence. The text is
produced by deterministic templates — rebuildable, checkable against the log, never
stored (a report is a projection you read, not a fact you write). A model may polish
the wording as an optional layer on top (`polish`), never as the source of truth.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from eidolon.institution import ADVISED, RECORDED, REQUESTED, SIGNED, DecisionState, load
from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.kernel.facts import Fact
from eidolon.kernel.store import FactStore
from eidolon.money.trace import ledger_fact_ids, project_costs
from eidolon.work import facts as F
from eidolon.work.projection import ProjectRow
from eidolon.work.service import WorkService
from eidolon.work.state import MissionState, ProjectState

LANGS = ("zh", "en")
SECTIONS = ("asked", "done", "decided", "cost", "stuck", "next")

_T: dict[str, dict[str, str]] = {
    "zh": {
        "section.asked": "要了什么",
        "section.done": "做了什么",
        "section.decided": "谁在哪决定了什么",
        "section.cost": "花了多少",
        "section.stuck": "卡在哪",
        "section.next": "下一步需要你什么",
        "mission.created": "{when}，{who} 提出使命《{title}》：{goal}",
        "mission.criteria": "验收标准：{criteria}",
        "mission.constraints": "约束：{constraints}",
        "mission.budget": "预算 {budget}{deadline}",
        "mission.deadline": "，期限 {deadline}",
        "mission.accepted": "{when}，{who} 接受了使命{reason}",
        "mission.rejected": "{when}，{who} 拒绝了使命{reason}",
        "mission.pending": "使命还在等 {role} 接受",
        "project.created": "{when}，立项《{title}》，项目经理 {manager}，预算 {budget}",
        "project.plan": "{when}，方案通过（{n} 个任务），由 {signers} 签字",
        "project.plan_pending": "方案还在等 {role} 签字",
        "project.delivered": "{when}，项目交付，{who} 验收",
        "project.cancelled": "{when}，项目取消{reason}",
        "task.done": "任务《{title}》由 {assignee} 完成，{reviewer} 评审通过{rework}{artifacts}",
        "task.rework": "（返工 {n} 次）",
        "task.artifacts": "，产出 {paths}",
        "task.open": "任务《{title}》{status}{assignee}",
        "task.status.todo": "待开始",
        "task.status.in_progress": "进行中",
        "task.status.in_review": "待评审",
        "task.status.blocked": "被卡住",
        "task.status.failed": "失败",
        "task.status.cancelled": "已取消",
        "task.assignee": "，负责人 {assignee}",
        "done.nothing": "还没有开始做",
        "decision": "{gate}：{status}，{by}{brief}",
        "decision.signed_by": "{signers} 签字",
        "decision.unsigned": "尚无人签字",
        "decision.escalated": "，已升级 {n} 次",
        "decision.brief": "；{advisor} 建议{rec}：{summary}",
        "decision.none": "没有需要记录的决定",
        "verdict.approve": "批准",
        "verdict.reject": "拒绝",
        "verdict.rework": "返工",
        "verdict.pass": "通过",
        "verdict.escalate": "上报",
        "status.pending": "待定",
        "status.approved": "已批准",
        "status.rejected": "已拒绝",
        "status.rework": "要求返工",
        "cost.total": "到目前花了 {total}，{budget_part}",
        "cost.budget": "占预算 {budget} 的 {pct}%",
        "cost.no_budget": "没有预算上限",
        "cost.task": "《{title}》花了 {amount}",
        "cost.management": "管理（规划、指派、评审、建议）花了 {amount}",
        "cost.none": "还没有花钱",
        "stuck.task": "任务《{title}》{status}{reason}",
        "stuck.reason": "：{reason}",
        "stuck.rework": "任务《{title}》已返工 {n} 次",
        "stuck.escalated": "{gate} 没人按时决定，已升级到 {role}",
        "stuck.waiting_agent": "{gate} 还在等 {role} 决定",
        "stuck.none": "没有卡住的地方",
        "stuck.triage": "{who} 建议{what}：{summary}（{status}）",
        "disposition.reopen": "重开",
        "disposition.reassign": "改派给 {assignee}",
        "disposition.cancel": "取消",
        "disposition.split": "拆成 {n} 个小任务",
        "status.exhausted": "建议均被否，等人处理",
        "next.decision": "{gate} 等你决定{brief}",
        "next.brief": "——{advisor} 建议{rec}：{summary}",
        "next.deliver_wait": "所有任务已完成，等项目经理提出交付",
        "next.nothing": "暂时不需要你做什么",
        "next.done": "项目已交付，没有了",
        "next.mission_pending": "使命等 {role} 接受",
        "by.system": "系统",
    },
    "en": {
        "section.asked": "What was asked",
        "section.done": "What was done",
        "section.decided": "Who decided what",
        "section.cost": "What it cost",
        "section.stuck": "Where it is stuck",
        "section.next": "What is needed from you",
        "mission.created": "{when}: {who} proposed the mission “{title}”: {goal}",
        "mission.criteria": "Success criteria: {criteria}",
        "mission.constraints": "Constraints: {constraints}",
        "mission.budget": "Budget {budget}{deadline}",
        "mission.deadline": ", deadline {deadline}",
        "mission.accepted": "{when}: {who} accepted the mission{reason}",
        "mission.rejected": "{when}: {who} rejected the mission{reason}",
        "mission.pending": "The mission still waits for {role} to accept it",
        "project.created": "{when}: project “{title}” opened, manager {manager}, budget {budget}",
        "project.plan": "{when}: plan approved ({n} tasks), signed by {signers}",
        "project.plan_pending": "The plan still waits for {role} to sign",
        "project.delivered": "{when}: project delivered, accepted by {who}",
        "project.cancelled": "{when}: project cancelled{reason}",
        "task.done": (
            "Task “{title}” done by {assignee}, review passed by {reviewer}{rework}{artifacts}"
        ),
        "task.rework": " (after {n} rework rounds)",
        "task.artifacts": ", producing {paths}",
        "task.open": "Task “{title}” is {status}{assignee}",
        "task.status.todo": "not started",
        "task.status.in_progress": "in progress",
        "task.status.in_review": "waiting for review",
        "task.status.blocked": "blocked",
        "task.status.failed": "failed",
        "task.status.cancelled": "cancelled",
        "task.assignee": ", owner {assignee}",
        "done.nothing": "Nothing has been started yet",
        "decision": "{gate}: {status}, {by}{brief}",
        "decision.signed_by": "signed by {signers}",
        "decision.unsigned": "nobody has signed yet",
        "decision.escalated": ", escalated {n}×",
        "decision.brief": "; {advisor} recommends {rec}: {summary}",
        "decision.none": "No decisions to record",
        "verdict.approve": "approve",
        "verdict.reject": "reject",
        "verdict.rework": "rework",
        "verdict.pass": "pass",
        "verdict.escalate": "escalate",
        "status.pending": "pending",
        "status.approved": "approved",
        "status.rejected": "rejected",
        "status.rework": "sent back",
        "cost.total": "Spent so far: {total}, {budget_part}",
        "cost.budget": "{pct}% of the {budget} budget",
        "cost.no_budget": "no budget cap",
        "cost.task": "“{title}” cost {amount}",
        "cost.management": "Management (planning, assignment, review, advice) cost {amount}",
        "cost.none": "Nothing spent yet",
        "stuck.task": "Task “{title}” is {status}{reason}",
        "stuck.reason": ": {reason}",
        "stuck.rework": "Task “{title}” has been sent back {n} times",
        "stuck.escalated": "{gate} was not decided in time and went up to {role}",
        "stuck.waiting_agent": "{gate} still waits for {role}",
        "stuck.none": "Nothing is stuck",
        "stuck.triage": "{who} proposes to {what}: {summary} ({status})",
        "disposition.reopen": "reopen it",
        "disposition.reassign": "hand it to {assignee}",
        "disposition.cancel": "cancel it",
        "disposition.split": "split it into {n} smaller tasks",
        "status.exhausted": "every proposal rejected; waiting for a person",
        "next.decision": "{gate} waits for your decision{brief}",
        "next.brief": " — {advisor} recommends {rec}: {summary}",
        "next.deliver_wait": "Every task is done; waiting for the manager to propose delivery",
        "next.nothing": "Nothing is needed from you right now",
        "next.done": "The project is delivered; nothing more",
        "next.mission_pending": "The mission waits for {role} to accept it",
        "by.system": "the system",
    },
}


def _t(lang: str, key: str, **kw: Any) -> str:
    table = _T.get(lang) or _T["zh"]
    return (table.get(key) or _T["en"][key]).format(**kw)


@dataclass
class Line:
    text: str
    facts: list[UUID] = field(default_factory=list)
    link: dict[str, str] | None = None  # {"kind": decision|task|run|artifact|mission, "id": ...}

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "facts": [str(f) for f in self.facts], "link": self.link}


@dataclass
class Section:
    key: str
    title: str
    lines: list[Line] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "title": self.title, "lines": [ln.as_dict() for ln in self.lines]}


@dataclass
class Report:
    kind: str  # project | mission
    id: UUID
    company_id: UUID
    title: str
    status: str
    lang: str
    as_of_fact_id: UUID | None
    sections: list[Section]
    needs_you: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "id": str(self.id),
            "company_id": str(self.company_id),
            "title": self.title,
            "status": self.status,
            "lang": self.lang,
            "as_of_fact_id": str(self.as_of_fact_id) if self.as_of_fact_id else None,
            "sections": [s.as_dict() for s in self.sections],
            "needs_you": self.needs_you,
        }

    def to_text(self) -> str:
        out = [f"# {self.title} ({self.status})"]
        for s in self.sections:
            out.append(f"\n## {s.title}")
            for ln in s.lines:
                out.append(f"- {ln.text}")
        return "\n".join(out) + "\n"

    def fact_ids(self) -> set[UUID]:
        return {f for s in self.sections for ln in s.lines for f in ln.facts}


# -- formatting --------------------------------------------------------------------------


def _when(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "?"


def _money(micro: int, currency: str | None) -> str:
    return f"{micro / 1_000_000:.2f} {currency or ''}".strip()


def _units(units: int | None, currency: str | None) -> str:
    if units is None:
        return "—"
    return _money(units * MICRO_PER_UNIT, currency)


def _who(fact: Fact, lang: str) -> str:
    return fact.actor.id or _t(lang, "by.system")


def _join(items: Iterable[str], lang: str) -> str:
    sep = "、" if lang == "zh" else ", "
    return sep.join(items)


def _reason(reason: str | None, lang: str) -> str:
    if not reason:
        return ""
    return f"：{reason}" if lang == "zh" else f": {reason}"


def _signers(d: DecisionState, lang: str) -> str:
    parts = []
    for s in d.signatures:
        verdict = _t(lang, "verdict." + s.verdict)
        parts.append(
            f"{s.person_id}（{s.role}，{verdict}）"
            if lang == "zh"
            else f"{s.person_id} ({s.role}, {verdict})"
        )
    return _join(parts, lang)


# -- gathering ---------------------------------------------------------------------------


def _company_currency(db: Session, store: FactStore, company_id: UUID) -> str | None:
    from eidolon.identity import IdentityService

    try:
        return IdentityService(store).company(db, company_id).currency
    except Exception:  # noqa: BLE001 - a company without identity facts (tests) has no currency
        return None


def _decision_facts(db: Session, store: FactStore, d: DecisionState) -> list[UUID]:
    return [
        f.id
        for f in store.by_correlation(
            db, d.decision_id, kinds=[REQUESTED, SIGNED, RECORDED, ADVISED]
        )
    ]


def _decisions_about(
    db: Session,
    store: FactStore,
    company_id: UUID,
    *,
    project_id: UUID | None = None,
    task_ids: Iterable[UUID] = (),
    mission_id: UUID | None = None,
) -> list[DecisionState]:
    wanted_tasks = {str(t) for t in task_ids}
    out: list[DecisionState] = []
    for f in store.stream(db, kinds=[REQUESTED], company_id=company_id, limit=100_000):
        subj = f.payload.get("subject") or {}
        if (
            (project_id is not None and subj.get("project_id") == str(project_id))
            or (subj.get("task_id") in wanted_tasks)
            or (mission_id is not None and subj.get("mission_id") == str(mission_id))
        ):
            out.append(load(db, f.correlation_id, store=store))
    return out


def _brief_part(d: DecisionState, lang: str, key: str) -> str:
    if d.brief is None:
        return ""
    return _t(
        lang,
        key,
        advisor=d.brief.advisor or "?",
        rec=_t(lang, "verdict." + d.brief.recommendation),
        summary=d.brief.summary,
    )


def _needs_you_item(d: DecisionState) -> dict[str, Any]:
    return {
        "decision_id": str(d.decision_id),
        "gate": d.gate,
        "requested_at": d.requested_at.isoformat() if d.requested_at else None,
        "recommendation": d.brief.recommendation if d.brief else None,
        "summary": d.brief.summary if d.brief else None,
        "advisor": d.brief.advisor if d.brief else None,
    }


# -- project -------------------------------------------------------------------------------


def project_report(
    db: Session,
    project_id: UUID,
    *,
    lang: str = "zh",
    store: FactStore | None = None,
    svc: WorkService | None = None,
) -> Report:
    store = store or FactStore()
    svc = svc or WorkService(store)
    facts = store.by_correlation(db, project_id, kinds=F.PROJECT_KINDS)
    state = ProjectState.from_facts(project_id, facts)
    mission = svc.mission(db, state.mission_id) if state.mission_id else None
    mission_facts = (
        store.by_correlation(db, state.mission_id, kinds=F.MISSION_KINDS)
        if state.mission_id
        else []
    )
    decisions = _decisions_about(
        db, store, state.company_id, project_id=project_id, task_ids=state.tasks.keys()
    )
    costs = project_costs(db, project_id, store=store)
    currency = costs.currency or _company_currency(db, store, state.company_id)
    sections = [
        _asked(state, facts, mission, mission_facts, decisions, lang, currency, store, db),
        _done(state, facts, lang),
        _decided(
            decisions, lang, store, db, {str(t.task_id): t.title for t in state.tasks.values()}
        ),
        _cost(state, costs, lang, store, db, currency),
        _stuck(state, facts, decisions, lang),
        _next(state, decisions, lang),
    ]
    return Report(
        kind="project",
        id=project_id,
        company_id=state.company_id,
        title=state.title,
        status=state.status,
        lang=lang,
        as_of_fact_id=state.last_fact_id,
        sections=sections,
        needs_you=[
            _needs_you_item(d)
            for d in decisions
            if d.status == "pending" and d.resolution.decider == "human"
        ],
    )


def _of_kind(facts: list[Fact], kind: str, **match: str) -> list[Fact]:
    return [
        f for f in facts if f.kind == kind and all(f.payload.get(k) == v for k, v in match.items())
    ]


def _asked(
    state: ProjectState,
    facts: list[Fact],
    mission: MissionState | None,
    mission_facts: list[Fact],
    decisions: list[DecisionState],
    lang: str,
    currency: str | None,
    store: FactStore,
    db: Session,
) -> Section:
    sec = Section("asked", _t(lang, "section.asked"))
    if mission is not None:
        sec.lines.extend(_mission_lines(mission, mission_facts, lang, currency))
    created = _of_kind(facts, F.PROJECT_CREATED)
    if created:
        c = created[0]
        sec.lines.append(
            Line(
                _t(
                    lang,
                    "project.created",
                    when=_when(c.created_at),
                    title=state.title,
                    manager=state.manager or "—",
                    budget=_units(state.budget, currency),
                ),
                [c.id],
            )
        )
    plan = _of_kind(facts, F.PROJECT_PLAN_APPROVED)
    plan_decision = next((d for d in decisions if d.gate == "plan.approve"), None)
    if plan:
        p = plan[0]
        ids = [p.id] + (_decision_facts(db, store, plan_decision) if plan_decision else [])
        sec.lines.append(
            Line(
                _t(
                    lang,
                    "project.plan",
                    when=_when(p.created_at),
                    n=len(p.payload.get("task_ids") or state.tasks),
                    signers=_signers(plan_decision, lang) if plan_decision else "—",
                ),
                ids,
                {"kind": "decision", "id": p.payload["decision_id"]},
            )
        )
    elif plan_decision is not None and plan_decision.status == "pending":
        sec.lines.append(
            Line(
                _t(lang, "project.plan_pending", role=plan_decision.resolution.role or "?"),
                _decision_facts(db, store, plan_decision),
                {"kind": "decision", "id": str(plan_decision.decision_id)},
            )
        )
    return sec


def _mission_lines(
    mission: MissionState, mission_facts: list[Fact], lang: str, currency: str | None
) -> list[Line]:
    lines: list[Line] = []
    created = _of_kind(mission_facts, F.MISSION_CREATED)
    if created:
        c = created[0]
        lines.append(
            Line(
                _t(
                    lang,
                    "mission.created",
                    when=_when(c.created_at),
                    who=_who(c, lang),
                    title=mission.title,
                    goal=mission.goal,
                ),
                [c.id],
                {"kind": "mission", "id": str(mission.mission_id)},
            )
        )
        if mission.success_criteria:
            lines.append(
                Line(
                    _t(lang, "mission.criteria", criteria=_join(mission.success_criteria, lang)),
                    [c.id],
                )
            )
        if mission.constraints:
            lines.append(
                Line(
                    _t(lang, "mission.constraints", constraints=_join(mission.constraints, lang)),
                    [c.id],
                )
            )
        if mission.budget is not None or mission.deadline:
            lines.append(
                Line(
                    _t(
                        lang,
                        "mission.budget",
                        budget=_units(mission.budget, currency),
                        deadline=_t(lang, "mission.deadline", deadline=mission.deadline)
                        if mission.deadline
                        else "",
                    ),
                    [c.id],
                )
            )
    for kind, key in (
        (F.MISSION_ACCEPTED, "mission.accepted"),
        (F.MISSION_REJECTED, "mission.rejected"),
    ):
        for f in _of_kind(mission_facts, kind):
            lines.append(
                Line(
                    _t(
                        lang,
                        key,
                        when=_when(f.created_at),
                        who=_who(f, lang),
                        reason=_reason(f.payload.get("reason"), lang),
                    ),
                    [f.id],
                    {"kind": "decision", "id": f.payload["decision_id"]}
                    if f.payload.get("decision_id")
                    else None,
                )
            )
    return lines


def _task_facts(facts: list[Fact], task_id: UUID) -> list[UUID]:
    return [f.id for f in facts if f.payload.get("task_id") == str(task_id)]


def _done(state: ProjectState, facts: list[Fact], lang: str) -> Section:
    sec = Section("done", _t(lang, "section.done"))
    for t in sorted(state.tasks.values(), key=lambda t: t.created_at or datetime.min):
        ids = _task_facts(facts, t.task_id)
        link = {"kind": "task", "id": str(t.task_id)}
        if t.status == "done":
            passed = next((r for r in reversed(t.reviews) if r.get("verdict") == "pass"), None)
            paths = sorted(a.path for a in state.artifacts.values() if a.task_id == t.task_id)
            sec.lines.append(
                Line(
                    _t(
                        lang,
                        "task.done",
                        title=t.title,
                        assignee=t.assignee or "—",
                        reviewer=(passed or {}).get("reviewer") or "—",
                        rework=_t(lang, "task.rework", n=t.rework_count) if t.rework_count else "",
                        artifacts=_t(lang, "task.artifacts", paths=_join(paths, lang))
                        if paths
                        else "",
                    ),
                    ids,
                    link,
                )
            )
        else:
            sec.lines.append(
                Line(
                    _t(
                        lang,
                        "task.open",
                        title=t.title,
                        status=_t(lang, "task.status." + t.status),
                        assignee=_t(lang, "task.assignee", assignee=t.assignee)
                        if t.assignee
                        else "",
                    ),
                    ids,
                    link,
                )
            )
    delivered = _of_kind(facts, F.PROJECT_DELIVERED)
    for f in delivered:
        sec.lines.append(
            Line(
                _t(lang, "project.delivered", when=_when(f.created_at), who=_who(f, lang)),
                [f.id],
                {"kind": "decision", "id": f.payload["decision_id"]},
            )
        )
    for f in _of_kind(facts, F.PROJECT_CANCELLED):
        sec.lines.append(
            Line(
                _t(
                    lang,
                    "project.cancelled",
                    when=_when(f.created_at),
                    reason=_reason(f.payload.get("reason"), lang),
                ),
                [f.id],
                {"kind": "decision", "id": f.payload["decision_id"]},
            )
        )
    if not sec.lines:
        sec.lines.append(
            Line(_t(lang, "done.nothing"), [state.last_fact_id] if state.last_fact_id else [])
        )
    return sec


def _decided(
    decisions: list[DecisionState],
    lang: str,
    store: FactStore,
    db: Session,
    titles: dict[str, str] | None = None,
) -> Section:
    sec = Section("decided", _t(lang, "section.decided"))
    titles = titles or {}
    for d in sorted(decisions, key=lambda d: d.requested_at or datetime.min):
        title = titles.get(str(d.subject.get("task_id")))
        gate = d.gate + ((f"《{title}》" if lang == "zh" else f" (“{title}”)") if title else "")
        by = (
            _t(lang, "decision.signed_by", signers=_signers(d, lang))
            if d.signatures
            else _t(lang, "decision.unsigned")
        )
        if d.escalations:
            by += _t(lang, "decision.escalated", n=d.escalations)
        sec.lines.append(
            Line(
                _t(
                    lang,
                    "decision",
                    gate=gate,
                    status=_t(lang, "status." + d.status),
                    by=by,
                    brief=_brief_part(d, lang, "decision.brief"),
                ),
                _decision_facts(db, store, d),
                {"kind": "decision", "id": str(d.decision_id)},
            )
        )
    if not sec.lines:
        sec.lines.append(Line(_t(lang, "decision.none")))
    return sec


def _cost(
    state: ProjectState, costs, lang: str, store: FactStore, db: Session, currency: str | None
) -> Section:
    sec = Section("cost", _t(lang, "section.cost"))
    ids = ledger_fact_ids(db, state.project_id, store=store)
    if costs.total_micro == 0:
        sec.lines.append(Line(_t(lang, "cost.none"), ids))
        return sec
    budget_micro = state.budget * MICRO_PER_UNIT if state.budget else 0
    budget_part = (
        _t(
            lang,
            "cost.budget",
            budget=_units(state.budget, currency),
            pct=round(100 * costs.total_micro / budget_micro, 1),
        )
        if budget_micro
        else _t(lang, "cost.no_budget")
    )
    sec.lines.append(
        Line(
            _t(
                lang,
                "cost.total",
                total=_money(costs.total_micro, currency),
                budget_part=budget_part,
            ),
            ids,
        )
    )
    for t in costs.tasks:
        if t.micro:
            sec.lines.append(
                Line(
                    _t(lang, "cost.task", title=t.title, amount=_money(t.micro, currency)),
                    [],
                    {"kind": "task", "id": str(t.task_id)},
                )
            )
    if costs.management_micro:
        sec.lines.append(
            Line(_t(lang, "cost.management", amount=_money(costs.management_micro, currency)))
        )
    return sec


def _stuck(
    state: ProjectState, facts: list[Fact], decisions: list[DecisionState], lang: str
) -> Section:
    sec = Section("stuck", _t(lang, "section.stuck"))
    for t in state.tasks.values():
        link = {"kind": "task", "id": str(t.task_id)}
        if t.status in ("failed", "blocked"):
            last = next(
                (
                    f
                    for f in reversed(facts)
                    if f.payload.get("task_id") == str(t.task_id)
                    and f.kind in (F.TASK_FAILED, F.TASK_BLOCKED, F.TASK_REVIEWED)
                ),
                None,
            )
            sec.lines.append(
                Line(
                    _t(
                        lang,
                        "stuck.task",
                        title=t.title,
                        status=_t(lang, "task.status." + t.status),
                        reason=_t(lang, "stuck.reason", reason=last.payload.get("reason"))
                        if last is not None and last.payload.get("reason")
                        else "",
                    ),
                    [last.id] if last else _task_facts(facts, t.task_id),
                    link,
                )
            )
        elif t.rework_count >= 2 and t.status != "done":
            sec.lines.append(
                Line(
                    _t(lang, "stuck.rework", title=t.title, n=t.rework_count),
                    _task_facts(facts, t.task_id),
                    link,
                )
            )
        if t.status in ("failed", "blocked"):
            proposals = [
                d
                for d in decisions
                if d.gate == "task.triage" and d.subject.get("task_id") == str(t.task_id)
            ]
            for d in sorted(proposals, key=lambda d: d.requested_at or datetime.min)[-1:]:
                what = _t(
                    lang,
                    "disposition." + str(d.subject.get("disposition")),
                    assignee=d.subject.get("assignee") or "?",
                    n=len(d.subject.get("new_tasks") or ()),
                )
                sec.lines.append(
                    Line(
                        _t(
                            lang,
                            "stuck.triage",
                            who=d.proposer or "?",
                            what=what,
                            summary=d.brief.summary if d.brief else d.subject.get("reason", ""),
                            status=_t(lang, "status." + d.status),
                        ),
                        [d.last_fact_id] if d.last_fact_id else [],
                        {"kind": "decision", "id": str(d.decision_id)},
                    )
                )
    for d in decisions:
        if d.status != "pending":
            continue
        link = {"kind": "decision", "id": str(d.decision_id)}
        if d.escalations:
            sec.lines.append(
                Line(
                    _t(lang, "stuck.escalated", gate=d.gate, role=d.resolution.role or "?"),
                    [d.last_fact_id] if d.last_fact_id else [],
                    link,
                )
            )
        elif d.resolution.decider == "agent":
            sec.lines.append(
                Line(
                    _t(lang, "stuck.waiting_agent", gate=d.gate, role=d.resolution.role or "?"),
                    [d.last_fact_id] if d.last_fact_id else [],
                    link,
                )
            )
    if not sec.lines:
        sec.lines.append(Line(_t(lang, "stuck.none")))
    return sec


def _next(state: ProjectState, decisions: list[DecisionState], lang: str) -> Section:
    sec = Section("next", _t(lang, "section.next"))
    for d in decisions:
        if d.status == "pending" and d.resolution.decider == "human":
            sec.lines.append(
                Line(
                    _t(
                        lang, "next.decision", gate=d.gate, brief=_brief_part(d, lang, "next.brief")
                    ),
                    [d.last_fact_id] if d.last_fact_id else [],
                    {"kind": "decision", "id": str(d.decision_id)},
                )
            )
    if sec.lines:
        return sec
    if state.status == "delivered":
        sec.lines.append(Line(_t(lang, "next.done")))
    elif (
        state.status == "active"
        and state.tasks
        and all(t.status == "done" for t in state.tasks.values())
    ):
        sec.lines.append(Line(_t(lang, "next.deliver_wait")))
    else:
        sec.lines.append(Line(_t(lang, "next.nothing")))
    return sec


# -- mission -------------------------------------------------------------------------------


def mission_report(
    db: Session,
    mission_id: UUID,
    *,
    lang: str = "zh",
    store: FactStore | None = None,
    svc: WorkService | None = None,
) -> Report:
    store = store or FactStore()
    svc = svc or WorkService(store)
    mission = svc.mission(db, mission_id)
    mission_facts = store.by_correlation(db, mission_id, kinds=F.MISSION_KINDS)
    currency = None
    rows = db.scalars(
        select(ProjectRow)
        .where(ProjectRow.mission_id == mission_id)
        .order_by(ProjectRow.created_at)
    ).all()
    reports = [project_report(db, r.project_id, lang=lang, store=store, svc=svc) for r in rows]
    mission_ids = {f.id for f in mission_facts}
    if reports:
        currency = project_costs(db, reports[0].id, store=store).currency
    currency = currency or _company_currency(db, store, mission.company_id)
    asked = Section("asked", _t(lang, "section.asked"))
    asked.lines.extend(_mission_lines(mission, mission_facts, lang, currency))
    decisions = _decisions_about(db, store, mission.company_id, mission_id=mission_id)
    needs: list[dict[str, Any]] = [
        _needs_you_item(d)
        for d in decisions
        if d.status == "pending" and d.resolution.decider == "human"
    ]
    if mission.status == "proposed":
        pending = next(
            (d for d in decisions if d.gate == "mission.accept" and d.status == "pending"), None
        )
        role = pending.resolution.role if pending else "?"
        asked.lines.append(
            Line(
                _t(lang, "mission.pending", role=role),
                [pending.last_fact_id] if pending and pending.last_fact_id else [],
                {"kind": "decision", "id": str(pending.decision_id)} if pending else None,
            )
        )
    sections = [asked, _decided(decisions, lang, store, db)]
    for r in reports:
        for s in r.sections:
            if s.key == "asked":
                # the mission's own lines are already above; keep the project's
                s.lines = [ln for ln in s.lines if ln.facts and ln.facts[0] not in mission_ids]
            s.title = f"{r.title} · {s.title}"
            s.key = f"{s.key}:{r.id}"
            sections.append(s)
        needs.extend(r.needs_you)
    if not reports:
        nxt = Section("next", _t(lang, "section.next"))
        for d in decisions:
            if d.status == "pending" and d.resolution.decider == "human":
                nxt.lines.append(
                    Line(
                        _t(
                            lang,
                            "next.decision",
                            gate=d.gate,
                            brief=_brief_part(d, lang, "next.brief"),
                        ),
                        [d.last_fact_id] if d.last_fact_id else [],
                        {"kind": "decision", "id": str(d.decision_id)},
                    )
                )
        if not nxt.lines:
            nxt.lines.append(Line(_t(lang, "next.nothing")))
        sections.append(nxt)
    last = max((f.seq, f.id) for f in mission_facts)[1] if mission_facts else None
    return Report(
        kind="mission",
        id=mission_id,
        company_id=mission.company_id,
        title=mission.title,
        status=mission.status,
        lang=lang,
        as_of_fact_id=last,
        sections=sections,
        needs_you=needs,
    )


# -- optional polish ------------------------------------------------------------------------

POLISH_SYSTEM = {
    "zh": "把下面这份项目报告用一段大白话讲给老板听：先说现在到哪了，再说过程，最后说老板要做什么。"
    "只用报告里已有的事实和数字，不要加新内容。保留任务名和人名。",
    "en": "Tell the owner this project report as one short plain paragraph: where it stands, "
    "then how it got there, then what the owner must do. Use only facts and numbers already in "
    "the report; add nothing. Keep task names and people.",
}


async def polish(report: Report, provider, *, model: str) -> str:
    """One model call that rewrites the deterministic text. Returns the model's text;
    the caller decides whether to show it next to (never instead of) the source."""
    from eidolon.agent.providers import CallContext, ChatRequest, Message, TextDelta
    from eidolon.kernel.facts import Actor

    request = ChatRequest(
        model=model,
        system=POLISH_SYSTEM.get(report.lang, POLISH_SYSTEM["en"]),
        messages=(Message(role="user", content=report.to_text()),),
        max_tokens=8192,  # thinking-mode models spend output tokens before the prose
        temperature=0.2,
        context=CallContext(report.company_id, Actor.system(), correlation_id=report.id),
    )
    parts: list[str] = []
    async for event in provider.complete(request):
        if isinstance(event, TextDelta):
            parts.append(event.text)
    return "".join(parts).strip()
