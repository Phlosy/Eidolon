"""通用/专业能力目录 —— 内置数据 + 全局种子（docs/competency-system.md §3/§4）。

设计要点：

- 目录是**数据不是列**：`competency_domains`（code=general 的通用能力域 + 专业领域）下挂
  `competency_definitions`。要加专业领域 = 往库里插 domain + definitions 行（company_id
  NULL 为全局内置；公司自定义 = company 行），**不需要 migration**。
- 内置行 `built_in=True`、`company_id IS NULL`，全公司共享、只读语义；改动走数据而非代码。
- code 是稳定机器标识（UI i18n / 职位要求引用它），name/description 给人看。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.competency import CompetencyDefinition, CompetencyDomain
from app.models.enums import CompetencyKind


@dataclass(frozen=True)
class CatalogCompetency:
    code: str
    name: str
    description: str = ""
    facets: list[str] = field(default_factory=list)
    evidence_kinds: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CatalogDomain:
    code: str
    name: str
    kind: str
    description: str = ""
    competencies: tuple[CatalogCompetency, ...] = ()
    order_index: int = 0


GENERAL_CODE = "general"
GENERAL_DOMAIN_NAME = "通用能力"

GENERAL_COMPETENCIES = (
    CatalogCompetency(
        "analysis_problem_solving",
        "分析与问题解决",
        "理解/拆解问题、抓住关键矛盾、归因、设计可行方案",
    ),
    CatalogCompetency(
        "planning_organization",
        "规划与组织",
        "任务拆解、优先级、里程碑、依赖、时间与资源安排",
    ),
    CatalogCompetency("execution", "执行能力", "把计划推进到成果、处理阻塞、闭环交付"),
    CatalogCompetency(
        "communication",
        "沟通表达",
        "文字与口头表达、汇报、需求澄清、解释复杂问题、按受众调整",
    ),
    CatalogCompetency(
        "collaboration",
        "协作能力",
        "配合、交接、知识共享、peer review、冲突处理",
    ),
    CatalogCompetency(
        "management_leadership",
        "管理与领导",
        "分派、协调、调度、培养、监督、推动团队达成目标",
    ),
    CatalogCompetency(
        "decision_making",
        "决策能力",
        "方案比较、trade-off、不确定判断、风险权衡、为决定负责",
    ),
    CatalogCompetency(
        "learning_growth",
        "学习与成长",
        "从任务中学习、沉淀可复用知识、学新工具、吸收反馈并转化",
    ),
    CatalogCompetency(
        "quality_reliability",
        "质量与可靠性",
        "遵循验收标准、自检、稳定交付、低返工、低严重错误",
    ),
    CatalogCompetency(
        "efficiency_resource_awareness",
        "效率与资源控制",
        "token/时间/算力/成本效率、避免无意义重复",
    ),
)

PROFESSIONAL_DOMAINS = (
    CatalogDomain(
        code="software_engineering",
        name="软件工程",
        kind=CompetencyKind.professional.value,
        description="Software Engineering 专业能力",
        order_index=1,
        competencies=(
            CatalogCompetency("frontend_engineering", "前端工程", "Web 前端架构、交互与性能实现"),
            CatalogCompetency("backend_engineering", "后端工程", "服务端设计、API 与数据层实现"),
            CatalogCompetency("system_architecture", "系统架构", "整体结构、模块边界与演进设计"),
            CatalogCompetency("testing", "测试工程", "测试策略、用例设计与验证闭环"),
            CatalogCompetency("devops", "DevOps", "构建、部署、可观测性与运维自动化"),
            CatalogCompetency("database", "数据库", "数据建模、查询优化与存储设计"),
            CatalogCompetency("security", "安全工程", "威胁建模、加固与安全编码"),
            CatalogCompetency("performance", "性能工程", "性能分析、瓶颈定位与优化"),
            CatalogCompetency("code_quality", "代码质量", "可读性、可维护性与评审标准"),
        ),
    ),
    CatalogDomain(
        code="research",
        name="研究",
        kind=CompetencyKind.professional.value,
        description="Research 专业能力",
        order_index=2,
        competencies=(
            CatalogCompetency("information_retrieval", "信息检索", "检索策略与信息获取效率"),
            CatalogCompetency("source_evaluation", "信源评估", "判断来源可信度与时效"),
            CatalogCompetency("evidence_synthesis", "证据综合", "跨来源归纳、比较与收敛结论"),
            CatalogCompetency("literature_review", "文献综述", "系统梳理论域现状"),
            CatalogCompetency("experiment_design", "实验设计", "假设、变量与可验证的实验方案"),
            CatalogCompetency("technical_writing", "技术写作", "结构清晰、可复核的技术文档"),
        ),
    ),
    CatalogDomain(
        code="product",
        name="产品",
        kind=CompetencyKind.professional.value,
        description="Product 专业能力",
        order_index=3,
        competencies=(
            CatalogCompetency("requirements_analysis", "需求分析", "把模糊诉求转成可验收需求"),
            CatalogCompetency("product_design", "产品设计", "功能、交互与体验设计"),
            CatalogCompetency("prioritization", "优先级决策", "在约束下排定价值顺序"),
            CatalogCompetency("acceptance_design", "验收设计", "把需求变成可执行验收标准"),
            CatalogCompetency("stakeholder_communication", "干系人沟通", "对齐期望、管理反馈"),
        ),
    ),
    CatalogDomain(
        code="management",
        name="管理",
        kind=CompetencyKind.professional.value,
        description="Management 专业能力",
        order_index=4,
        competencies=(
            CatalogCompetency("strategic_planning", "战略规划", "方向设定与阶段路径"),
            CatalogCompetency("delegation", "授权与分工", "把任务交给对的人并授信"),
            CatalogCompetency("resource_allocation", "资源分配", "把有限人力/算力/时间派到刀刃上"),
            CatalogCompetency("risk_management", "风险管理", "识别、评估与处置风险"),
            CatalogCompetency("team_development", "团队发展", "培养人、建设梯队"),
            CatalogCompetency("organizational_coordination", "组织协调", "跨团队/跨部门协同"),
        ),
    ),
    CatalogDomain(
        code="quality_assurance",
        name="质量保障",
        kind=CompetencyKind.professional.value,
        description="QA 专业能力",
        order_index=5,
        competencies=(
            CatalogCompetency("test_design", "测试设计", "等价类/边界/场景设计用例"),
            CatalogCompetency("test_automation", "测试自动化", "E2E/回归自动化与稳定性"),
            CatalogCompetency("defect_analysis", "缺陷分析", "定位根因、评估影响范围"),
            CatalogCompetency("regression_testing", "回归测试", "防止旧行为被新改动破坏"),
            CatalogCompetency("acceptance_testing", "验收测试", "对照验收标准逐项验证"),
            CatalogCompetency("quality_assurance", "质量保证", "质量流程与门禁设计"),
        ),
    ),
)


def general_domain() -> CatalogDomain:
    return CatalogDomain(
        code=GENERAL_CODE,
        name=GENERAL_DOMAIN_NAME,
        kind=CompetencyKind.general.value,
        description="人人适用的通用能力（10 维，第一版固定）",
        order_index=0,
        competencies=tuple(GENERAL_COMPETENCIES),
    )


def builtin_catalog() -> Iterator[CatalogDomain]:
    yield general_domain()
    yield from PROFESSIONAL_DOMAINS


def ensure_global_catalog(db: Session) -> int:
    """幂等种子：把内置目录写进库（company_id NULL = 全局）。返回新建对象数。"""
    created = 0
    for domain in builtin_catalog():
        existing_domain = db.scalar(
            select(CompetencyDomain).where(
                CompetencyDomain.company_id.is_(None),
                CompetencyDomain.code == domain.code,
            )
        )
        if existing_domain is None:
            existing_domain = CompetencyDomain(
                company_id=None,
                code=domain.code,
                name=domain.name,
                kind=domain.kind,
                description=domain.description,
                order_index=domain.order_index,
                built_in=True,
            )
            db.add(existing_domain)
            db.flush()
            created += 1
        for index, competency in enumerate(domain.competencies):
            exists = db.scalar(
                select(CompetencyDefinition.id).where(
                    CompetencyDefinition.domain_id == existing_domain.id,
                    CompetencyDefinition.code == competency.code,
                )
            )
            if exists is None:
                db.add(
                    CompetencyDefinition(
                        domain_id=existing_domain.id,
                        code=competency.code,
                        name=competency.name,
                        description=competency.description,
                        facets=list(competency.facets),
                        evidence_kinds=list(competency.evidence_kinds),
                        order_index=index,
                        built_in=True,
                    )
                )
                created += 1
    return created
