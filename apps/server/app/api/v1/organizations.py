"""组织 / 编制 API —— 职位域的读与写（`position-system.md §8` 的 P4b 子集）。

路径刻意用 `/organizations/...`：v0.4 已经把 `/positions` 用在旧职位表上
（`app/api/v1/lifecycle.py`），那套语义是"部门里的一个岗位标签"。新域的定义/编制
若共用同一路径，`GET /positions` 会返回两种完全不同的形状 —— 路由按注册顺序匹配，
这种"看谁先注册"的歧义必须避免。v0.4 的 `/positions` 在 v18 随表一起退役。

这里不做的是文档 §8 里属于后面阶段的端点：`/slots/{id}/candidates` 与
`/positions/fit-estimate`（P6/P7 fit 推荐）、`PATCH /definitions/{id}`（P6 目录编辑）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.scope import resolve_company_id
from app.core.database import get_db
from app.schemas.position import (
    PositionDefinitionIn,
    PositionDefinitionOut,
    PositionSlotOut,
    SlotActionIn,
    SlotIn,
)
from app.services import position_service

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("/tree")
def organization_tree(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> dict:
    """部门 → 编制 → 在任者。占用态全部当场派生（无物化列）。"""
    return position_service.organization(db, company_id)


@router.get("/vacancies", response_model=list[PositionSlotOut])
def vacancies(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """真实空缺（`slot.administrative_status IN (planned, active)` 且无在任者）。"""
    return position_service.vacant_slots(db, company_id)


@router.get("/definitions", response_model=list[PositionDefinitionOut])
def list_definitions(
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """模板列表。`slot_count`/`vacant_count`/`package_slugs` 是派生的，见服务层出口。"""
    return position_service.definitions_out(db, company_id)


@router.post(
    "/definitions",
    response_model=PositionDefinitionOut,
    status_code=status.HTTP_201_CREATED,
)
def create_definition(
    payload: PositionDefinitionIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> PositionDefinitionOut:
    """自定义模板只能落在本 scope（`build_definition_layers()` 决定它不会被继承污染）。"""
    return position_service.create_definition(db, payload, company_id)


@router.post(
    "/definitions/{definition_id}/slots",
    response_model=list[PositionSlotOut],
    status_code=status.HTTP_201_CREATED,
)
def open_slots(
    definition_id: int,
    payload: SlotIn,
    company_id: int | None = Depends(resolve_company_id),
    db: Session = Depends(get_db),
) -> list:
    """开编制后立刻回完整形状（含 occupancy_status=vacant），前端不必再查一次。"""
    created = position_service.open_slots(db, definition_id, payload, company_id)
    return position_service.slots_out(db, created)


@router.get("/slots/{slot_id}", response_model=PositionSlotOut)
def get_slot(
    slot_id: int,
    db: Session = Depends(get_db),
):
    return position_service.get_slot(db, slot_id)


@router.post("/slots/{slot_id}/freeze", response_model=PositionSlotOut)
def freeze_slot(
    slot_id: int,
    payload: SlotActionIn,
    db: Session = Depends(get_db),
):
    """FROZEN 既不接收新人，也不算空缺 —— 所以冻结不会把编制"腾"给招聘建议。"""
    position_service.freeze_slot(db, slot_id, payload.reason)
    return position_service.slot_out(db, slot_id)


@router.post("/slots/{slot_id}/close", response_model=PositionSlotOut)
def close_slot(
    slot_id: int,
    payload: SlotActionIn,
    db: Session = Depends(get_db),
):
    """CLOSED 终局（要恢复必须重新定义）：撤销编制，但不解任在任者。"""
    position_service.close_slot(db, slot_id, payload.reason)
    return position_service.slot_out(db, slot_id)


@router.post("/slots/{slot_id}/activate", response_model=PositionSlotOut)
def activate_slot(
    slot_id: int,
    payload: SlotActionIn,
    db: Session = Depends(get_db),
):
    position_service.activate_slot(db, slot_id, payload.reason)
    return position_service.slot_out(db, slot_id)
