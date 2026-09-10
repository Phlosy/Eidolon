"""Person 读面（T2.1）—— 统一人员投影。

`PersonReadService` = `app/talent/person/read_model.py` 的模块级读函数：
培养 UI / 市场 UI / 员工 UI 共用同一投影（设计 §9），禁止各自聚合。

- `read_model.person_profile(...)`：identity / traits / competencies / knowledge_summary
  （+ 可选 timeline / evidence）；
- `access.visible_person_or_404(...)`：自有 person 判定（owner_company 或本公司 employee）。

语义铁律：无证据 → null（禁止 0）；score 与 confidence 并列不混算；知识只给统计不给正文。
"""

from app.talent.person.access import is_own_person, visible_person_or_404
from app.talent.person.read_model import (
    DEFAULT_INCLUDES,
    OPTIONAL_INCLUDES,
    competencies_out,
    evidence_out,
    identity_out,
    knowledge_summary_out,
    person_profile,
    timeline_out,
    traits_out,
)

__all__ = [
    "DEFAULT_INCLUDES",
    "OPTIONAL_INCLUDES",
    "competencies_out",
    "evidence_out",
    "identity_out",
    "knowledge_summary_out",
    "is_own_person",
    "person_profile",
    "timeline_out",
    "traits_out",
    "visible_person_or_404",
]
