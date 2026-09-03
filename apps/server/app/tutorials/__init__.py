"""Declarative tutorial definitions rendered by the web tutorial engine.

两个彼此独立的教程：
- ``COMPANY_FOUNDING_TUTORIAL``：公司基础配置，通关即 OPERATING；
- ``FIRST_PROJECT_PRACTICE``：Classic Snake 实战，可整体跳过且零 token 成本。
"""

from app.tutorials.company_founding import (
    COMPANY_FOUNDING_TUTORIAL,
    TUTORIAL_CENTER,
    TUTORIAL_LIBRARY,
)
from app.tutorials.first_project_practice import FIRST_PROJECT_PRACTICE

# id -> 定义。服务层按 id 取，不再把教程写死成"只有一个"。
DEFINITIONS: dict[str, dict] = {
    COMPANY_FOUNDING_TUTORIAL["id"]: COMPANY_FOUNDING_TUTORIAL,
    FIRST_PROJECT_PRACTICE["id"]: FIRST_PROJECT_PRACTICE,
}

CORE_TUTORIAL_ID = COMPANY_FOUNDING_TUTORIAL["id"]
PRACTICE_TUTORIAL_ID = FIRST_PROJECT_PRACTICE["id"]

__all__ = [
    "COMPANY_FOUNDING_TUTORIAL",
    "CORE_TUTORIAL_ID",
    "DEFINITIONS",
    "FIRST_PROJECT_PRACTICE",
    "PRACTICE_TUTORIAL_ID",
    "TUTORIAL_CENTER",
    "TUTORIAL_LIBRARY",
]
