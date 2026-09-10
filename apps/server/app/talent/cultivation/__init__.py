"""Cultivation（T1 培养子系统）—— 模板（数据）与引擎（阶段推进/自由养成）。

角色 = Person + 扩展表（docs/cultivation-system-design.md §1）；产出复用员工学习链路
（services/learning.produce_learning_outputs），证据走 normalize.upsert_evidence 的
校验/幂等通道，教育来源分级（D4）。
"""
