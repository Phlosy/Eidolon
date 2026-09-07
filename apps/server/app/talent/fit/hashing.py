"""Fit inputs_hash —— 可追溯：同输入 + 同引擎/策略版本 ⇒ 同结果（P8 §22）。"""

from __future__ import annotations

import hashlib
import json

from app.talent.fit.policy import (
    POSITION_FIT_ENGINE_VERSION,
    POSITION_FIT_POLICY_VERSION,
)


def inputs_hash(
    *,
    employee_id: int,
    position_definition_id: int,
    profile_version: int | None,
    requirements: list[dict],
    competencies: list[dict],
) -> str:
    """规范化输入指纹。包含：员工/职位、画像版本、需求（含权重/门槛/关键位）、
    员工能力（score/confidence/last_assessed）、引擎与策略版本。"""
    payload = {
        "employee_id": employee_id,
        "position_definition_id": position_definition_id,
        "profile_version": profile_version,
        "engine_version": POSITION_FIT_ENGINE_VERSION,
        "policy_version": POSITION_FIT_POLICY_VERSION,
        "requirements": sorted(
            requirements,
            key=lambda item: (item["competency_definition_id"], item["requirement_id"]),
        ),
        "competencies": sorted(
            competencies,
            key=lambda item: item["competency_definition_id"],
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
