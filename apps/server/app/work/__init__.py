"""M2 工作与组织运行域（docs/m2-agent-work-runtime-design.md）。

本包在 M2.0 只承载**契约层**（`contracts.py`）：决策边界、职位三段式、
RoleContext 形状、记忆平面、DecisionRecord 字段、verdict 边界、工作根形态、
DAG 正确性校验、不变量注册表。

M2.1 起实现层按阶段进入：
    role_context.py（M2.2）· tools.py（M2.3）· decisions.py（M2.4）
    task_graph.py（M2.5）· artifacts.py（M2.6）· reviews.py（M2.7）
"""

from app.work import contracts

__all__ = ["contracts"]
