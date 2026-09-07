"""Assessment domain (P6): profiles / criteria / results / expectations.

规格：docs/assessment-system.md（四表 + 链）+ docs/evidence-pipeline.md。
P5 已先行落地 `assessment_runs` 作为确定性重算的审计锚点；P6 补齐其余：

    AssessmentProfile（考核档案：一个岗位怎么考核，code + version 唯一 ⇒ 模板版本化）
      └── AssessmentCriterion（考核维度；一条可贡献给多个能力）
            └── AssessmentCriterionCompetency（criterion → competency，带 contribution_weight）
    AssessmentResult（一次 run 的逐条观测/贡献 —— 解释“为什么是 72”）
    CompetencyExpectation（工作项/职位模板期望验证哪些能力；PRIMARY/SUPPORTING/OPTIONAL）

铁律（都有测试）：

- Profile 版本化：AssessmentRun 指向当时使用的 profile_version；模板更新不改历史含义。
- Criterion 可映射多个 Competency（一对多 + 权重），不压成一对一。
- Position 只提供 Expectation Template，**不产生 Evidence** —— 真实工作才产生。
- 能力分数只能从 Validated Evidence 经确定性引擎得到；AssessmentResult 是解释层，
  不是又一个“真相”。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow


class AssessmentProfile(TimestampMixin, Base):
    """考核档案（版本化模板）。

    `code` 标识模板谱系（如 software_engineer），`version` 从 1 递增；`(code, version)`
    唯一 —— AssessmentRun 记录当时用的 profile_id + version，模板更新不影响历史结果含义。
    """

    __tablename__ = "assessment_profiles"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_assessment_profile_code_version"),
    )

    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    code: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    #: position | initial —— 面向职位考核 / 初始评估
    applies_to_kind: Mapped[str] = mapped_column(String(30), default="position")
    position_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("position_definitions.id"), nullable=True
    )
    #: 该档案最少需要多少条证据才给出分数（低于阈值 = 保持 UNRATED，不编分）
    min_evidence_count: Mapped[int] = mapped_column(Integer, default=1)
    #: 证据新近度衰减半衰期（天）
    half_life_days: Mapped[float] = mapped_column(Float, default=90.0)
    algorithm_version: Mapped[str] = mapped_column(String(50), default="assessment-profile-v1")
    built_in: Mapped[bool] = mapped_column(Boolean, default=True)


class AssessmentCriterion(TimestampMixin, Base):
    """一个考核维度。`weight` 0..1（同 profile 内合计≈1）；可贡献给多个能力。"""

    __tablename__ = "assessment_criteria"
    __table_args__ = (UniqueConstraint("profile_id", "code", name="uq_assessment_criterion_code"),)

    profile_id: Mapped[int] = mapped_column(ForeignKey("assessment_profiles.id"), index=True)
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    #: 该维度只接受哪些证据来源；空 = 不限
    evidence_kinds: Mapped[list] = mapped_column(JSON, default=list)


class AssessmentCriterionCompetency(Base):
    """criterion → competency 的中间表（带 contribution_weight / evidence_type）。

    拍板命名（workforce-domain-refactor.md §11 #4）：`contribution_weight` +
    `evidence_type`（让“同一证据不重复供证”成为声明）。
    """

    __tablename__ = "assessment_criterion_competencies"
    __table_args__ = (
        UniqueConstraint(
            "criterion_id",
            "competency_definition_id",
            name="uq_criterion_competency",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    criterion_id: Mapped[int] = mapped_column(ForeignKey("assessment_criteria.id"), index=True)
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    #: 该 criterion 的观测分按多少比例贡献给这个能力（Σ ≤ 1）
    contribution_weight: Mapped[float] = mapped_column(Float, default=1.0)
    #: 证据类型限定（如只把执行类证据计给 execution）；空 = 不限
    evidence_type: Mapped[str] = mapped_column(String(30), default="")
    order_index: Mapped[int] = mapped_column(Integer, default=0)


class AssessmentResult(TimestampMixin, Base):
    """一次 run 的逐条输出 —— “为什么 Communication 是 72”的解释层。

    两类行（kind）：
    - `criterion`：criterion 观测分（observed/confidence/evidence ids）—— 对应
      assessment-system.md §1.4 的“criterion 观测分”；
    - `contribution`：该 criterion 对某个能力的贡献（observed × contribution_weight），
      记录在案以便回答“这次考核把 testing 从 78 抬到 81”。
    """

    __tablename__ = "assessment_results"

    run_id: Mapped[int] = mapped_column(ForeignKey("assessment_runs.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="criterion")
    criterion_id: Mapped[int | None] = mapped_column(
        ForeignKey("assessment_criteria.id"), nullable=True, index=True
    )
    competency_definition_id: Mapped[int | None] = mapped_column(
        ForeignKey("competency_definitions.id"), nullable=True, index=True
    )
    observed_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    #: 本 run 对能力分的净变化（contribution 行）；criterion 行为 NULL
    contribution: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)


class CompetencyExpectation(Base):
    """工作项/职位模板期望验证哪些能力。

    scope_kind：`task` / `position_definition`（未来 project_phase）。Position 只提供
    Expectation Template；真实工作完成才产生 Evidence（Collector 按期望映射到能力）。
    """

    __tablename__ = "competency_expectations"
    __table_args__ = (
        UniqueConstraint(
            "scope_kind",
            "scope_id",
            "competency_definition_id",
            name="uq_competency_expectation",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope_kind: Mapped[str] = mapped_column(String(30))
    scope_id: Mapped[int] = mapped_column(Integer)
    competency_definition_id: Mapped[int] = mapped_column(
        ForeignKey("competency_definitions.id"), index=True
    )
    role: Mapped[str] = mapped_column(String(20), default="supporting")
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
