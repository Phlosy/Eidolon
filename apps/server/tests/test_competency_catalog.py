"""P5：能力目录种子与“数据可扩展”契约（docs/competency-system.md §3/§4）。

锁的内容：
1. 种子正确性：通用能力域恰好 10 条、专业域 5 个且各自的维度数精确匹配规格；
2. 幂等：重复 ensure 不产生新行；
3. 数据可扩展：新增专业领域 = 插库，**不需要 migration**；
4. 职位能力要求（position_competency_requirements）schema 可用（正式匹配计算在 P8/P10）；
5. 全库回归：目录行全部挂在全局（company_id NULL = built-in 共享目录）。
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from app.competency import catalog
from app.models.competency import (
    CompetencyDefinition,
    CompetencyDomain,
    PositionCompetencyRequirement,
)
from app.models.enums import CompetencyKind

GENERAL_CODES = {
    "analysis_problem_solving",
    "planning_organization",
    "execution",
    "communication",
    "collaboration",
    "management_leadership",
    "decision_making",
    "learning_growth",
    "quality_reliability",
    "efficiency_resource_awareness",
}

PROFESSIONAL_COUNTS = {
    "software_engineering": 9,
    "research": 6,
    "product": 5,
    "management": 6,
    "quality_assurance": 6,
}


def _definitions_of(db, domain_code: str) -> list[CompetencyDefinition]:
    return list(
        db.scalars(
            sa.select(CompetencyDefinition)
            .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
            .where(CompetencyDomain.code == domain_code)
            .order_by(CompetencyDefinition.order_index)
        )
    )


def test_general_catalog_has_exactly_the_ten_first_version_dimensions(db):
    domain = db.scalar(
        sa.select(CompetencyDomain).where(CompetencyDomain.code == catalog.GENERAL_CODE)
    )
    assert domain is not None
    assert domain.company_id is None, "内置目录必须是全局行"
    assert domain.kind == CompetencyKind.general.value
    definitions = _definitions_of(db, catalog.GENERAL_CODE)
    assert [item.code for item in definitions] == [
        item.code for item in catalog.GENERAL_COMPETENCIES
    ]
    assert {item.code for item in definitions} == GENERAL_CODES


def test_professional_catalog_matches_the_spec_counts(db):
    for code, expected_count in PROFESSIONAL_COUNTS.items():
        domain = db.scalar(
            sa.select(CompetencyDomain).where(
                CompetencyDomain.company_id.is_(None), CompetencyDomain.code == code
            )
        )
        assert domain is not None, f"缺专业域 {code}"
        assert domain.kind == CompetencyKind.professional.value
        definitions = _definitions_of(db, code)
        assert len(definitions) == expected_count, f"{code} 维度数应为 {expected_count}"
        assert len({item.code for item in definitions}) == expected_count, f"{code} code 重复"


def test_all_builtin_rows_are_global_and_unique(db):
    """内置行 company_id 全 NULL；每个 domain 下 code 唯一（无 migration 即可扩展）。"""
    domains = db.scalars(
        sa.select(CompetencyDomain).where(CompetencyDomain.built_in.is_(True))
    ).all()
    assert domains, "内置目录没有种子"
    for domain in domains:
        assert domain.company_id is None, domain.code
        codes = _definitions_of(db, domain.code)
        assert len({item.code for item in codes}) == len(codes)


def test_catalog_seed_is_idempotent(db):
    before_domains = db.scalar(sa.select(sa.func.count()).select_from(CompetencyDomain))
    before_defs = db.scalar(sa.select(sa.func.count()).select_from(CompetencyDefinition))
    created = catalog.ensure_global_catalog(db)
    assert created == 0, f"重复种子不该新增行：{created}"
    db.rollback()  # ensure 可能 flush；回滚后计数不受影响（实际无新增）
    assert db.scalar(sa.select(sa.func.count()).select_from(CompetencyDomain)) == before_domains
    assert db.scalar(sa.select(sa.func.count()).select_from(CompetencyDefinition)) == before_defs


def test_new_professional_domain_needs_no_migration(db, default_company_id):
    """公司自定义专业域 = 数据操作（company 行 domain + defs），结构上不需要 migration。"""
    domain = CompetencyDomain(
        company_id=default_company_id,
        code="ai_platform",
        name="AI 平台",
        kind=CompetencyKind.professional.value,
        description="公司自定义专业域（测试）",
        built_in=False,
    )
    db.add(domain)
    db.flush()
    db.add(
        CompetencyDefinition(
            domain_id=domain.id,
            code="agent_workflow_engineering",
            name="Agent 工作流工程",
            description="编排、记忆与工具调用设计",
            built_in=False,
            order_index=0,
        )
    )
    db.commit()
    found = db.scalar(
        sa.select(CompetencyDefinition).where(
            CompetencyDefinition.code == "agent_workflow_engineering"
        )
    )
    assert found is not None and found.domain_id == domain.id
    # 全局目录不受公司自定义行影响
    general_count = db.scalar(
        sa.select(sa.func.count())
        .select_from(CompetencyDefinition)
        .join(CompetencyDomain, CompetencyDomain.id == CompetencyDefinition.domain_id)
        .where(
            CompetencyDomain.code == catalog.GENERAL_CODE,
            CompetencyDomain.company_id.is_(None),
        )
    )
    assert general_count == 10


def test_position_competency_requirement_schema_is_usable(db):
    """需求侧 schema 可用（正式匹配计算放 P8/P10，这里只验证落库/约束）。"""
    definition_id = db.scalar(
        sa.select(CompetencyDefinition.id).where(CompetencyDefinition.code == "testing")
    )
    position_id = db.scalar(
        sa.text("SELECT id FROM position_definitions WHERE code = 'engineer' LIMIT 1")
    )
    assert definition_id is not None and position_id is not None
    db.add(
        PositionCompetencyRequirement(
            position_definition_id=position_id,
            competency_definition_id=definition_id,
            minimum_score=70,
            weight=1.0,
            required=True,
        )
    )
    db.commit()
    row = db.scalar(
        sa.select(PositionCompetencyRequirement).where(
            PositionCompetencyRequirement.competency_definition_id == definition_id,
            PositionCompetencyRequirement.position_definition_id == position_id,
        )
    )
    assert row is not None
    assert row.minimum_score == 70
    # 同一位（职位,能力）只能有一条 —— 唯一约束生效
    db.add(
        PositionCompetencyRequirement(
            position_definition_id=position_id,
            competency_definition_id=definition_id,
            minimum_score=80,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        db.commit()
    db.rollback()
