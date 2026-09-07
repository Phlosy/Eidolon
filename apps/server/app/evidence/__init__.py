"""Evidence pipeline (P6): collector → candidate → normalize → assessment trigger.

架构见 docs/evidence-pipeline.md。硬约束：
  - 业务系统只产生事实；Collector 解释为能力证据候选；
  - 候选必须经 Normalizer 校验 + 幂等落库（同一事件重复消费只有一条证据）；
  - 失败也允许成为证据（低 signal），但绝不"失败一次 → score-10"（走同一聚合器）。
"""
