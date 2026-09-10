"""Traits 读出（/employees/{id}/traits 的序列化出口）。

读人格是 brain 域的合法职责 —— 本模块放这里（而不是放进能力写路径 services/competency.py，
避免触发"写路径不得 import brain"的架构守卫）。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.brain.registry import TRAIT_REGISTRY
from app.brain.traits import BrainTraits
from app.repositories import runtimes as runtime_repo


def employee_traits_out(db: Session, employee_id: int) -> list[dict]:
    """8 维人格的 UI 数据契约（code/label/description/value/display/affects_execution）。

    值一律来自 `BrainTraits`（缺失键 = 注册表默认）；`affects_execution` = 是否已接行为。
    只描述"倾向怎样工作"，不带任何能力加成/成功率语义（展示侧禁止换算）。
    """
    # R1.1：brain 读口径已切 person_id（repo 入口解析，带旧口径回落）
    brain = runtime_repo.get_brain(db, employee_id)
    return _traits_out(brain)


def person_traits_out(db: Session, person_id: int) -> list[dict]:
    """person 口径人格读出（T1.3 培养 UI：角色没有 employee 行）。

    培养期人格由 T1.2 的 `initialize_character_brain` + 际遇偏移写入，本函数只读。
    """
    brain = runtime_repo.get_brain_by_person(db, person_id)
    return _traits_out(brain)


def _traits_out(brain) -> list[dict]:
    traits = BrainTraits.from_brain(brain)
    out: list[dict] = []
    for spec in TRAIT_REGISTRY.values():
        value = float(traits[spec.key])
        out.append(
            {
                "code": spec.key,
                "label": spec.label,
                "description": spec.description,
                "value": value,
                "display": int(round(value * 100)),
                "affects_execution": bool(spec.affects),
            }
        )
    return out
