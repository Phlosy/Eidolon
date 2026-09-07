"""Competency foundation (P5) —— Talent Profile & Competency 数据基座。

领域规格：docs/competency-system.md / docs/assessment-system.md；上级 ADR：
docs/workforce-domain-refactor.md（ADR-6 Trait/Competency/Skill 三张不同的表、
ADR-7 能力只能被证明不能被分配）、docs/architecture.md（ADR-12 派生字段）。

四条不可破坏的语义（都有测试）：

1. **Trait ≠ Competency ≠ Skill**：`EmployeeBrain.traits` 是行为倾向（0..1，JSON）；
   `skills` 是具体可复用做法；`employee_competencies` 是**被证据证明的能力**。
   三者不互相换算，也不允许任何代码路径用 Trait 给 Competency 赋值。
2. **能力只能被证明**：`employee_competencies` 的行只能由 Assessment（聚合器）写入，
   没有直接 PATCH score 的入口。无证据 = 不落行 = 查询呈现 `unrated`（score=null）。
3. **Score ≠ Confidence**：score 是当前能力估计（0-100），confidence 是这个估计
   有多少证据支撑（0..1）。新员工没有任何证据 ⇒ score=null / confidence=null /
   status=unrated —— **绝不随机生成 60~90**（competency 写路径 import random 会被
   架构守卫红）。
4. **证据可追溯**：能力任何变化都能回链到 `competency_evidence` 行（task / project /
   test / review / artifact / user_feedback / peer_review / assessment / learning /
   skill_usage）。

目录扩展性：`competency_domains` / `competency_definitions` 是**数据不是列**。
`company_id IS NULL` 的行 = 全局内置目录（所有公司共享，只读）；公司要扩自己的专业
目录 = 新建 company 行 domain + definitions，**不需要 migration**（“每新增专业领域就
migration”被结构上排除）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import CompetencyKind, CompetencyStatus


class CompetencyDomain(TimestampMixin, Base):
    """能力域（通用能力是一个整体域；专业能力按领域划分，未来可在库里扩展）。"""

    __tablename__ = "competency_domains"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_competency_domain_company_code"),
    )

    # NULL = 全局内置目录（所有公司共享）；非空 = 该公司自定义域
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default=CompetencyKind.professional.value)
    description: Mapped[str] = mapped_column(Text, default="")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("competency_domains.id"), nullable=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)


class CompetencyDefinition(TimestampMixin, Base):
    """一条能力定义（一个可被考核、可被职位引用的能力维度）。"""

    __tablename__ = "competency_definitions"
    __table_args__ = (
        UniqueConstraint("domain_id", "code", name="uq_competency_definition_domain_code"),
    )

    domain_id: Mapped[int] = mapped_column(ForeignKey("competency_domains.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    #: 该能力覆盖内容的子项说明（给 UI/文档）
    facets: Mapped[list] = mapped_column(JSON, default=list)
    #: 允许哪些来源类型证明它（EvidenceSourceKind 的子集；空 = 不限）
    evidence_kinds: Mapped[list] = mapped_column(JSON, default=list)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)


class EmployeeCompetency(TimestampMixin, Base):
    """员工在某个能力维度上的当前状态。**只有评估写过才算数**：

    - 新员工**不预建行** —— 没有任何证据的维度在 API 上呈现为 `unrated`；
    - `score` / `confidence` 只能由 assessment 聚合器写入；手动 PATCH 不存在；
    - `trend` = 最近一次 run 与上一次 run 的能力分差（无历史为 NULL）。
    """

    __tablename__ = "employee_competencies"
    __table_args__ = (
        UniqueConstraint("employee_id", "competency_definition_id", name="uq_employee_competency"),
    )

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    # score NULL = 未评（不用 0 表示未评 —— 0 是"有证据表明很差"）
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    #: 参与最近一次聚合的证据数（冗余便于列表；真相仍是证据表）
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=CompetencyStatus.unrated.value)
    last_assessed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    trend: Mapped[int | None] = mapped_column(Integer, nullable=True)
    trend_window: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CompetencyEvidence(TimestampMixin, Base):
    """一条能力证据 —— 为什么"这个人是 82 分"的答案来源。"""

    __tablename__ = "competency_evidence"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    source_kind: Mapped[str] = mapped_column(String(30))
    #: 指向既有一等对象的 id（task/project/review/artifact/skill_usage…），可为空
    source_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 人类可读引用（如 "TEST-102 18/18 passed"）—— 数字必须能反查
    source_ref: Mapped[str] = mapped_column(String(500), default="")
    #: 这条证据出自哪次考核（P5 起 assessment_runs 先行落地；可为空 = 采集时未被 run 引用）
    assessment_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_runs.id"), nullable=True, index=True
    )
    #: 证据指向的"水平"观测（0-100）。NULL = 未判分（只记录"发生过"）
    signal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 证据可信度 0..1；NULL = 由聚合器按 source_kind 的固定质量表取值
    quality: Mapped[float | None] = mapped_column(Float, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AssessmentRun(TimestampMixin, Base):
    """一次（能力）考核/重算 —— P5 的确定性审计锚点。

    规格 assessment-system.md §1.3。P10 会在其上扩展 profile/criteria/results；
    本表先行落地的是**基础确定性重算**所需的骨架：谁、对谁、依据哪些证据、什么算法
    版本、输入指纹、输出。`inputs_hash` 是可追溯的硬保证：同一 hash 必须产出同一结果。
    """

    __tablename__ = "assessment_runs"

    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id"), index=True)
    #: P10 引入 profile/criteria 表后再接线（此时不加 FK，避免指向不存在的表）
    profile_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position_assignment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    triggered_by: Mapped[str] = mapped_column(String(30), default="recompute")
    status: Mapped[str] = mapped_column(String(20), default="completed")
    window_from: Mapped[datetime | None] = mapped_column(nullable=True)
    window_to: Mapped[datetime | None] = mapped_column(nullable=True)
    #: 采集到的证据 id 清单（可重放的关键）
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    algorithm_version: Mapped[str] = mapped_column(String(50), default="")
    inputs_hash: Mapped[str] = mapped_column(String(64), index=True)
    #: 本次 run 对每个能力维度的输出 {competency_definition_id: {...}}
    outputs: Mapped[dict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class PositionCompetencyRequirement(Base):
    """职位对能力的要求（schema 先行，P8/P10 才做正式匹配计算）。

    Position **不决定人拥有什么能力**，只声明需要什么 —— 本表是"需求侧"。
    """

    __tablename__ = "position_competency_requirements"
    __table_args__ = (
        UniqueConstraint(
            "position_definition_id",
            "competency_definition_id",
            name="uq_position_competency_requirement",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    position_definition_id: Mapped[int] = mapped_column(
        ForeignKey("position_definitions.id"), index=True
    )
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    #: 达标线；NULL = 没有分数下限，只需"被证明存在"
    minimum_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
