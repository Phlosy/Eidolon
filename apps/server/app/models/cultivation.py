"""Cultivation domain (T1.0)：Character / TrainingProgram / EducationEvent。

依据 docs/cultivation-system-design.md §1/§2：**不建新「人」实体**——角色就是 Person
（候选人 = 无所属关系的 PersonCore，concept-architecture §2.1），培养/市场语义按
D4.1 约定挂扩展表：

```
persons（身份聚合根，不动）
  └─ character_profiles  1:1   培养/市场域：origin、owner_company、lifecycle、identity_id
  └─ training_programs   1:N   一次培养实例：模板、当前阶段、资源消耗累计、RNG seed
  └─ education_events    1:N   履历事件流：课程/考试/际遇/项目，回链证据
```

D3 纪律：person_id 都不加 FK（项目不开 PRAGMA foreign_keys），1:1 靠
character_profiles.person_id 的唯一约束，完整性靠服务层 + 守卫
（tests/test_architecture_guards.py 的 person-only 行校验）。
"""

from datetime import datetime

from sqlalchemy import JSON, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, utcnow
from app.models.enums import CultivationState


class CharacterProfile(TimestampMixin, Base):
    """培养/市场域档案（1:1 挂 Person）。

    - identity_id：全局唯一身份标识（愿景 §6.1），创建即终身不变，T2/M1 交易锚点。
      生成规则：`CH-` + 12 位 Crockford base32（repositories/cultivation.py）。
    - origin：issued（官方发行，T2）/ trained（玩家自训）/ blank（空白自由养成起点）。
    - owner_company_id nullable：培养/持有它的公司；NULL = 在市场（T2 用）。
    - lifecycle：**培养状态轴**（T2 设计 §4）：cultivating → ready。
      `listed`/`hired` 曾是预留值，T2.0 起废弃：市场可发现性走 `market_listings`（T2.3）、
      任职走 `employments`；本列只允许 `CultivationState` 的两个值（守卫测试钉死）。
    """

    __tablename__ = "character_profiles"

    person_id: Mapped[int] = mapped_column(Integer, unique=True)
    identity_id: Mapped[str] = mapped_column(String(20), unique=True)
    origin: Mapped[str] = mapped_column(String(20))
    owner_company_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle: Mapped[str] = mapped_column(String(20), default=CultivationState.cultivating.value)


class TrainingProgram(TimestampMixin, Base):
    """一次培养实例（D3）：模板是数据不是代码（T1.1 接模板引擎）。

    rng_seed 在创建时落库——分布采样的确定性来源（同 seed 同结果，测试可注入）。
    resource_used 是资源消耗累计（M1 前的简单预算计数占位，JSON 台账）。
    """

    __tablename__ = "training_programs"

    person_id: Mapped[int] = mapped_column(Integer, index=True)
    # academic / vocational / self_taught / "" = 自由养成（无模板）
    template: Mapped[str] = mapped_column(String(30), default="")
    current_stage: Mapped[int] = mapped_column(Integer, default=0)
    resource_used: Mapped[dict] = mapped_column(JSON, default=dict)
    rng_seed: Mapped[str] = mapped_column(String(64))
    # active / completed / abandoned
    status: Mapped[str] = mapped_column(String(20), default="active")


class EducationEvent(TimestampMixin, Base):
    """履历事件流（D5 际遇也落这里，kind=fortune）。

    evidence_id 回链 competency_evidence（教育证据，D4 分级）——刻意不加 FK
    （D3 纪律），完整性靠服务层。
    """

    __tablename__ = "education_events"

    person_id: Mapped[int] = mapped_column(Integer, index=True)
    program_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # course / exam / fortune / project / internship / competition
    kind: Mapped[str] = mapped_column(String(30))
    topic: Mapped[str] = mapped_column(String(500), default="")
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    evidence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow)
