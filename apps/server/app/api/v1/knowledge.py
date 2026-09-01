"""/knowledge — retrieval (§6.3) + promotion proposals / review."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.knowledge import promotion
from app.models.enums import KnowledgeScope
from app.repositories import knowledge as knowledge_repo
from app.schemas.knowledge import KnowledgeItemOut, ProposalCreate, ReviewCreate

router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("", response_model=list[KnowledgeItemOut])
def list_knowledge(
    scope: str | None = None,
    topic: str | None = None,
    employee_id: int | None = None,
    db: Session = Depends(get_db),
) -> list[KnowledgeItemOut]:
    if scope == KnowledgeScope.private.value and employee_id is None:
        # 私有知识只能以 owner 视角检索 (§3.4.1)
        raise HTTPException(status_code=400, detail="employee_id is required when scope=private")
    items = knowledge_repo.list_knowledge_items(
        db, scope=scope, topic=topic, employee_id=employee_id
    )
    return [KnowledgeItemOut.model_validate(i) for i in items]


@router.post("/{item_id}/proposals", response_model=KnowledgeItemOut)
def propose_promotion(
    item_id: int, payload: ProposalCreate, db: Session = Depends(get_db)
) -> KnowledgeItemOut:
    item = knowledge_repo.get_knowledge_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="knowledge item not found")
    return KnowledgeItemOut.model_validate(promotion.propose(db, item, payload.target_scope.value))


@router.post("/{item_id}/review", response_model=KnowledgeItemOut)
def review_promotion(
    item_id: int, payload: ReviewCreate, db: Session = Depends(get_db)
) -> KnowledgeItemOut:
    item = knowledge_repo.get_knowledge_item(db, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="knowledge item not found")
    return KnowledgeItemOut.model_validate(promotion.review(db, item, payload.approve))
