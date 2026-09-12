"""M2.7 评审的查询层（`review_requests` / `review_facts`）。

只放查询与最小写入原语；**业务规则**（谁能评、结论怎么落地）在
`app/work/reviews.py` —— 那里是唯一口径，HTTP / 工具 / 执行面都走它。
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.review import ReviewFact, ReviewRequest


def get_request(db: Session, request_id: int) -> ReviewRequest | None:
    return db.get(ReviewRequest, int(request_id))


def list_requests(
    db: Session,
    *,
    task_id: int | None = None,
    project_id: int | None = None,
    reviewer_employee_id: int | None = None,
    status: str | None = None,
) -> list[ReviewRequest]:
    stmt = select(ReviewRequest).order_by(ReviewRequest.id.desc())
    if task_id is not None:
        stmt = stmt.where(ReviewRequest.task_id == int(task_id))
    if project_id is not None:
        stmt = stmt.where(ReviewRequest.project_id == int(project_id))
    if reviewer_employee_id is not None:
        stmt = stmt.where(ReviewRequest.reviewer_employee_id == int(reviewer_employee_id))
    if status is not None:
        stmt = stmt.where(ReviewRequest.status == status)
    return list(db.scalars(stmt))


def list_facts(db: Session, review_request_id: int) -> list[ReviewFact]:
    return list(
        db.scalars(
            select(ReviewFact)
            .where(ReviewFact.review_request_id == int(review_request_id))
            .order_by(ReviewFact.id)
        )
    )


def create_request(db: Session, **fields) -> ReviewRequest:
    row = ReviewRequest(**fields)
    db.add(row)
    db.flush()
    return row


def add_fact(
    db: Session, *, review_request_id: int, task_id: int, kind: str, payload: dict, source: str
) -> ReviewFact:
    row = ReviewFact(
        review_request_id=int(review_request_id),
        task_id=int(task_id),
        kind=kind,
        payload_json=payload,
        source=source,
    )
    db.add(row)
    db.flush()
    return row


__all__ = [
    "add_fact",
    "create_request",
    "get_request",
    "list_facts",
    "list_requests",
]
