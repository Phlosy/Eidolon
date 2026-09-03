"""Classic Snake 实战教程：可选练习，不是公司初始化的强制门。

与核心教程的关系（本次最重要的解耦）：
- 核心教程在 Engineer 配置完成即结束，并把公司推进到 OPERATING；
- 实战教程是独立的一行进度记录，Not Started / In Progress / Skipped / Completed
  都不影响 OPERATING；
- **跳过实战只写状态**：不建项目、不调 Agent、不启动 Runtime Session、
  不生成文档/Artifact，因此不产生任何 LLM token 成本。
  对应端点见 services/tutorial.py 的 skip_practice()。

实战内部的步骤仍然是 REQUIRED_ACTION：一旦开始，就要真跑完需求→设计→开发→
测试→验收→交付，不能靠点 Next 假装交付完成。
"""

from app.tutorials.schema import REQUIRED_ACTION, step

FIRST_PROJECT_PRACTICE = {
    "id": "first-project-practice",
    "version": 1,
    "title_key": "tutorials.firstProjectPractice.title",
    "kind": "PRACTICE",
    "allow_skip": True,  # 教程级可跳过；跳过零成本
    "sets_operating_stage": False,
    "stages": [
        {
            "id": "intake",
            "title_key": "tutorials.stages.intake",
            "steps": [
                step(
                    "create_project",
                    kind=REQUIRED_ACTION,
                    requirement="FIRST_PROJECT_CREATED",
                    route="/projects",
                    target_id="create-project",
                    placement="left",
                    interaction_mode="TARGET_ONLY",
                ),
            ],
        },
        {
            "id": "reviews",
            "title_key": "tutorials.stages.reviews",
            "steps": [
                step(
                    "requirements_review",
                    requirement="REQUIREMENTS_APPROVED",
                    route="/projects",
                    target_id="review-requirements",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "design_review",
                    requirement="DESIGN_APPROVED",
                    route="/projects",
                    target_id="review-design",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
            ],
        },
        {
            "id": "production",
            "title_key": "tutorials.stages.production",
            "steps": [
                step(
                    "development",
                    requirement="DEVELOPMENT_COMPLETED",
                    route="/projects",
                    target_id="phase-development",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "testing",
                    requirement="TESTING_COMPLETED",
                    route="/projects",
                    target_id="phase-testing",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "acceptance_review",
                    requirement="ACCEPTANCE_APPROVED",
                    route="/projects",
                    target_id="review-acceptance",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
            ],
        },
        {
            "id": "delivery",
            "title_key": "tutorials.stages.delivery",
            "steps": [
                step(
                    "delivery",
                    requirement="DELIVERY_COMPLETED",
                    route="/projects",
                    target_id="delivery-package",
                    placement="left",
                    interaction_mode="NON_BLOCKING",
                ),
            ],
        },
    ],
}
