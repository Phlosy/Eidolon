"""培养模板（T1.1，docs/cultivation-system-design.md §2 D3；愿景 §3.2）。

**模板是数据不是代码**：只提供概率倾向与默认学习路径，不决定结果——
「模板之间不设稀有度高低，三条路径产出不同形状的人才」。

字段语义（StageTemplate）：
- ``stage_id`` / ``name``：阶段标识（落 education_events.outcome）与显示名；
- ``topics``：本阶段主题采样池（引擎按 intensity 用确定性 RNG 抽样）；
- ``mode``：途径 → 既有 LearningMode（web_research / document_study / knowledge_review），
  产出原语与员工学习完全共用；
- ``intensity``：强度 = 本阶段覆盖的主题数（采样量）；
- ``duration_weeks``：时长（履历叙事字段，不计真实时间）；
- ``event_kind``：本阶段履历事件的 kind（course/exam/project/…，EducationEvent.kind）；
- ``evidence_kind``：产出证据的 source_kind（edu_course/edu_exam/edu_project…，D4 分级）；
- ``competency_code``：本阶段证据挂载的能力维度（全局目录 code）；
- ``signal_base`` / ``signal_spread``：证据 signal 的采样中位与噪声幅度
  （deterministic RNG 采样，signal = base ± spread）；
- ``trait_bias``：人格偏移权重（T1.2 人格成型用，结构预留，本轮不消费）；
- ``fortune``：际遇事件概率表（T1.2 填，结构预留，本轮恒空）。

首发三模板（愿景 §3.2 倾向表的直译）：学院派 = 四阶段学制、考试证据多；
职业派 = 义务教育 + 专业实训、项目证据早；自学派 = 非规范自学、证据少而散。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StageTemplate:
    stage_id: str
    name: str
    topics: tuple[str, ...]
    mode: str  # LearningMode 值
    intensity: int
    duration_weeks: int
    event_kind: str  # EducationEvent.kind
    evidence_kind: str  # EvidenceSourceKind 的 edu_* 值
    competency_code: str  # 全局能力目录 code
    signal_base: int
    signal_spread: int
    trait_bias: dict[str, float] = field(default_factory=dict)
    fortune: tuple[dict, ...] = ()  # T1.2 际遇概率表


@dataclass(frozen=True)
class CultivationTemplate:
    template_id: str
    name: str
    stages: tuple[StageTemplate, ...]


_GENERAL = "analysis_problem_solving"
_EXECUTION = "execution"

ACADEMIC = CultivationTemplate(
    template_id="academic",
    name="学院派",
    stages=(
        StageTemplate(
            stage_id="primary",
            name="小学",
            topics=("语文基础", "数学基础", "自然常识"),
            mode="document_study",
            intensity=2,
            duration_weeks=96,
            event_kind="course",
            evidence_kind="edu_course",
            competency_code=_GENERAL,
            signal_base=55,
            signal_spread=10,
            trait_bias={"conscientiousness": 0.05},
        ),
        StageTemplate(
            stage_id="junior",
            name="初中",
            topics=("代数", "几何", "物理入门", "英语阅读"),
            mode="document_study",
            intensity=3,
            duration_weeks=48,
            event_kind="course",
            evidence_kind="edu_course",
            competency_code=_GENERAL,
            signal_base=60,
            signal_spread=10,
            trait_bias={"conscientiousness": 0.05},
        ),
        StageTemplate(
            stage_id="senior",
            name="高中",
            topics=("函数与方程", "力学", "议论文写作", "综合测验"),
            mode="knowledge_review",
            intensity=3,
            duration_weeks=48,
            event_kind="exam",
            evidence_kind="edu_exam",
            competency_code=_GENERAL,
            signal_base=70,
            signal_spread=8,
            trait_bias={"conscientiousness": 0.1},
        ),
        StageTemplate(
            stage_id="university",
            name="大学",
            topics=("数据结构与算法", "操作系统原理", "专业英语", "毕业设计"),
            mode="web_research",
            intensity=3,
            duration_weeks=64,
            event_kind="exam",
            evidence_kind="edu_exam",
            competency_code=_GENERAL,
            signal_base=72,
            signal_spread=8,
            trait_bias={"conscientiousness": 0.1},
        ),
    ),
)

VOCATIONAL = CultivationTemplate(
    template_id="vocational",
    name="职业派",
    stages=(
        StageTemplate(
            stage_id="compulsory",
            name="九年义务教育",
            topics=("语文基础", "数学基础", "英语基础"),
            mode="document_study",
            intensity=2,
            duration_weeks=144,
            event_kind="course",
            evidence_kind="edu_course",
            competency_code=_GENERAL,
            signal_base=55,
            signal_spread=10,
        ),
        StageTemplate(
            stage_id="vocational_training",
            name="专业实训",
            topics=("工程制图与读图", "容器化部署实战", "前后端联调实战", "线上故障排查演练"),
            mode="web_research",
            intensity=3,
            duration_weeks=64,
            event_kind="project",
            evidence_kind="edu_project",
            competency_code=_EXECUTION,
            signal_base=75,
            signal_spread=8,
            trait_bias={"adaptability": 0.1},
        ),
    ),
)

SELF_TAUGHT = CultivationTemplate(
    template_id="self_taught",
    name="自学派",
    stages=(
        StageTemplate(
            stage_id="self_study",
            name="非规范自学",
            topics=("开源项目阅读", "博客与文档自学", "个人项目折腾", "社区问答潜水", "公开课旁听"),
            mode="web_research",
            intensity=4,
            duration_weeks=200,
            event_kind="course",
            evidence_kind="edu_course",
            competency_code=_GENERAL,
            signal_base=55,
            signal_spread=25,  # 置信度方差大：钻得深 vs 想当然
            trait_bias={"curiosity": 0.1, "creativity": 0.1, "risk_tolerance": 0.05},
        ),
    ),
)

#: template_id → 模板。空模板（""）= 自由养成，不在此表。
TEMPLATES: dict[str, CultivationTemplate] = {
    template.template_id: template for template in (ACADEMIC, VOCATIONAL, SELF_TAUGHT)
}
