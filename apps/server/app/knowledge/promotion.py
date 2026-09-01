"""Knowledge promotion: proposal / review. See docs/architecture.md §6.3.

Private knowledge is isolated by default; promotion requires Proposal + Review.
The target scope is stored on the item's ``proposed_scope`` column (proposals
have no dedicated table in the MVP).
"""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.events.bus import bus
from app.models.enums import KnowledgeScope, KnowledgeStatus
from app.models.knowledge import KnowledgeItem


def propose(db: Session, item: KnowledgeItem, target_scope: str) -> KnowledgeItem:
    target = KnowledgeScope(target_scope).value
    if target == KnowledgeScope.private.value:
        raise HTTPException(status_code=400, detail="cannot propose promotion to private scope")
    if item.status == KnowledgeStatus.proposed.value:
        raise HTTPException(status_code=409, detail="knowledge item already has a pending proposal")
    if item.scope == target:
        raise HTTPException(status_code=409, detail="knowledge item already at target scope")
    item.status = KnowledgeStatus.proposed.value
    item.proposed_scope = target
    db.commit()
    db.refresh(item)
    bus.publish(
        "knowledge.proposed",
        {"id": item.id, "from": item.scope, "target_scope": target},
        actor_employee_id=item.owner_employee_id,
    )
    return item


def review(db: Session, item: KnowledgeItem, approve: bool) -> KnowledgeItem:
    if item.status != KnowledgeStatus.proposed.value or not item.proposed_scope:
        raise HTTPException(status_code=409, detail="knowledge item has no pending proposal")
    if approve:
        item.scope = item.proposed_scope
        item.status = KnowledgeStatus.active.value
        item.proposed_scope = None
        db.commit()
        db.refresh(item)
        bus.publish(
            "knowledge.promoted",
            {"id": item.id, "scope": item.scope},
            actor_employee_id=item.owner_employee_id,
        )
    else:
        item.status = KnowledgeStatus.rejected.value
        item.proposed_scope = None
        db.commit()
        db.refresh(item)
    return item
