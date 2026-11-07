"""Reading memory. Visibility is enforced here, in the repository, for every
query (§4.3 "按 person 隔离在仓库层强制"): a viewer sees their own private
entries, their department's, and the company's — never another person's private
memory, whatever the caller asks for.

Ranking = text relevance × weight × importance factor × time decay (episodic only;
semantic knowledge does not fade). Full-text search now; a vector score slots in
as another factor later (pgvector).
"""

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Float, and_, case, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from eidolon.agent.memory.projection import SEARCH_CONFIG, MemoryEntryRow

EPISODIC_HALF_LIFE_DAYS = 30.0
_WORD = re.compile(r"\w+", re.UNICODE)


def _loosen(query: str, mode: str) -> str:
    if mode != "any":
        return query
    terms = [t for t in _WORD.findall(query) if len(t) > 1]
    return " or ".join(dict.fromkeys(terms)) or query


@dataclass(frozen=True, slots=True)
class Viewer:
    company_id: UUID
    person_id: str
    department_id: str | None = None


@dataclass(frozen=True, slots=True)
class Hit:
    memory_id: UUID
    layer: str
    scope: str
    kind: str
    title: str
    body: str
    tags: tuple[str, ...]
    person_id: str
    importance: float
    weight: float
    created_at: datetime
    score: float


class MemoryRepository:
    def __init__(self, *, half_life_days: float = EPISODIC_HALF_LIFE_DAYS):
        self.half_life_days = half_life_days

    def visible(self, viewer: Viewer):
        row = MemoryEntryRow
        clauses = [
            and_(row.scope == "private", row.person_id == viewer.person_id),
            row.scope == "company",
        ]
        if viewer.department_id:
            clauses.append(
                and_(row.scope == "department", row.department_id == viewer.department_id)
            )
        return and_(row.company_id == viewer.company_id, or_(*clauses))

    def search(
        self,
        db: Session,
        viewer: Viewer,
        query: str | None,
        *,
        layers: tuple[str, ...] | None = None,
        limit: int = 10,
        now: datetime | None = None,
        mode: str = "any",
    ) -> list[Hit]:
        """``mode="any"`` (recall from a long task prompt: any term matches, more terms rank
        higher); ``mode="all"`` is plain web-search semantics (every term required)."""
        row = MemoryEntryRow
        now = now or datetime.now(UTC)
        age_days = func.extract("epoch", literal(now) - row.created_at) / 86400.0
        decay = func.power(
            0.5, cast(age_days, Float) / self.half_life_days
        )  # halves every half-life
        time_factor = func.greatest(
            literal(0.05),  # old episodes stay reachable, just low
            cast(case((row.layer == "episodic", decay), else_=1.0), Float),
        )
        importance_factor = 0.5 + row.importance / 2.0
        if query and query.strip():
            tsq = func.websearch_to_tsquery(SEARCH_CONFIG, _loosen(query, mode))
            relevance = func.ts_rank_cd(row.search, tsq)
            where = [self.visible(viewer), row.search.op("@@")(tsq)]
        else:
            relevance = literal(1.0)
            where = [self.visible(viewer)]
        score = (relevance * row.weight * importance_factor * time_factor).label("score")
        stmt = select(row, score).where(*where)
        if layers:
            stmt = stmt.where(row.layer.in_(layers))
        stmt = stmt.order_by(score.desc(), row.created_at.desc()).limit(limit)
        return [self._hit(r, float(s)) for r, s in db.execute(stmt)]

    def get(self, db: Session, viewer: Viewer, memory_id: UUID) -> Hit | None:
        row = db.get(MemoryEntryRow, memory_id)
        if row is None or not self._can_see(viewer, row):
            return None
        return self._hit(row, 0.0)

    def _can_see(self, viewer: Viewer, row: MemoryEntryRow) -> bool:
        if row.company_id != viewer.company_id:
            return False
        if row.scope == "company":
            return True
        if row.scope == "department":
            return bool(viewer.department_id) and row.department_id == viewer.department_id
        return row.person_id == viewer.person_id

    @staticmethod
    def _hit(row: MemoryEntryRow, score: float) -> Hit:
        return Hit(
            memory_id=row.memory_id,
            layer=row.layer,
            scope=row.scope,
            kind=row.kind,
            title=row.title,
            body=row.body,
            tags=tuple(row.tags or ()),
            person_id=row.person_id,
            importance=row.importance,
            weight=row.weight,
            created_at=row.created_at,
            score=score if math.isfinite(score) else 0.0,
        )
