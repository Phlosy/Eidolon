"""/task-reviews —— 任务级技术评审（M2.7，设计 §12，W17 / RV1–RV8）。

**写面只有"给结论"这一件事**：给结论是 Reviewer 的判断，
系统在这条链路上**不产生**任何结论（没有自动通过端点，RV1）。

> 路由前缀刻意叫 `/task-reviews`：v0.5 交付域的**阶段门会议**已经在用
> `/reviews/{id}`（`ReviewMeeting`，人类阶段门）。两个面**不得互相替代**（W29），
> 所以连路由都分开 —— 同名同路径只会让调用方分不清自己在评什么。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.repositories import review as review_repo
from app.schemas.work import ReviewViewOut, VerdictIn
from app.work import reviews

router = APIRouter(prefix="/task-reviews", tags=["task-reviews"])


@router.get("/{request_id}", response_model=ReviewViewOut)
def get_review(request_id: int, db: Session = Depends(get_db)) -> ReviewViewOut:
    request = review_repo.get_request(db, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="review request not found")
    return ReviewViewOut(**reviews.review_view(db, request).as_dict())


@router.post("/{request_id}/verdict", response_model=ReviewViewOut)
def submit_verdict(
    request_id: int, payload: VerdictIn, db: Session = Depends(get_db)
) -> ReviewViewOut:
    """给出结论（人类管理动作）。结论只能由这条请求指定的评审人给出。"""
    request = review_repo.get_request(db, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="review request not found")
    try:
        reviews.submit_verdict(
            db,
            request=request,
            reviewer_employee_id=int(payload.reviewer_employee_id),
            verdict=payload.verdict,
            notes=payload.notes,
        )
    except reviews.ReviewError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.refresh(request)
    return ReviewViewOut(**reviews.review_view(db, request).as_dict())
