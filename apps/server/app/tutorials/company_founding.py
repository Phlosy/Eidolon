"""Company-founding tutorial definition; it observes real business services.

步骤只声明"要达成什么业务状态"（``requirement``）与"在界面上指向哪里"
（``route`` / ``target_id`` / ``placement`` / ``interaction_mode``）。
判定本身在 ``app.tutorials.requirements``，文案在 i18n（``title_key`` 等），
这里不写任何完成规则，也不写任何页面特判。
"""

from app.tutorials.schema import INFORMATION, OPTIONAL, REQUIRED, step

COMPANY_FOUNDING_TUTORIAL = {
    "id": "company-founding",
    "version": 2,
    "title_key": "tutorials.companyFounding.title",
    "description_key": "tutorials.companyFounding.description",
    "kind": REQUIRED,
    # 核心教程通关即代表公司可以进入经营；实战项目不在这里，
    # 见 first_project_practice —— 跳过它不影响 OPERATING。
    "sets_operating_stage": True,
    # 核心教程不能被"一键跳过"：它靠真实业务状态通关；
    # 可跳过的是单个 OPTIONAL_ACTION 步骤，以及整个实战教程。
    "allow_skip": False,
    "stages": [
        {
            "id": "welcome",
            "title_key": "tutorials.stages.welcome",
            "steps": [
                step(
                    "company_setup",
                    kind=INFORMATION,
                    requirement="COMPANY_CREATED",
                    route="/",
                    target_id="company-overview",
                    placement="right",
                    interaction_mode="FOCUS_ONLY",
                ),
            ],
        },
        {
            "id": "leadership",
            "title_key": "tutorials.stages.leadership",
            "steps": [
                step(
                    "hire_ceo",
                    why_key="steps.hire_ceo.why",
                    requirement="CEO_ACTIVE",
                    route="/employees",
                    target_id="hire-employee",
                    placement="left",
                    # 招聘向导内部的逐步指引：引擎按顺序聚光，但"完成"只由
                    # CEO_ACTIVE 这个真实状态决定 —— 指引不是通关条件。
                    # ui_hints 只负责"向导内部往哪儿看"，完成条件仍是 CEO_ACTIVE；
                    # target id 与 hire-wizard.tsx 里的 data-tutorial-target 一一对应。
                    metadata={
                        "ui_hints": [
                            {"target_id": "wizard-identity", "text_key": "hints.identity"},
                            {"target_id": "wizard-runtime", "text_key": "hints.pickRuntime"},
                            {"target_id": "wizard-provider", "text_key": "hints.pickProvider"},
                            {"target_id": "wizard-packages", "text_key": "hints.pickAccess"},
                            {"target_id": "wizard-confirm", "text_key": "hints.confirmOnboard"},
                        ]
                    },
                ),
                step(
                    "configure_ceo_runtime",
                    why_key="steps.configure_ceo_runtime.why",
                    requirement="CEO_RUNTIME_CONFIGURED",
                    route="/employees/{ceo_employee_id}",
                    target_id="employee-runtime-tab",
                    placement="top",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "configure_ceo_provider",
                    why_key="steps.configure_ceo_provider.why",
                    requirement="CEO_PROVIDER_CONFIGURED",
                    route="/employees/{ceo_employee_id}",
                    target_id="employee-provider-bind",
                    placement="top",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "configure_company_resources",
                    why_key="steps.configure_company_resources.why",
                    requirement="CEO_RESOURCES_PROVISIONED",
                    route="/employees/{ceo_employee_id}",
                    target_id="employee-accounts-tab",
                    placement="top",
                    interaction_mode="NON_BLOCKING",
                ),
            ],
        },
        {
            "id": "company_systems",
            "title_key": "tutorials.stages.companySystems",
            "steps": [
                step(
                    "cloud_docs",
                    requirement="COMPANY_DOCUMENT_CREATED",
                    route="/drive",
                    target_id="create-document",
                    placement="bottom",
                ),
                step(
                    "git_setup",
                    kind=OPTIONAL,
                    requirement="GIT_CONFIGURED",
                    route="/settings",
                    target_id="git-connection-create",
                    placement="top",
                    interaction_mode="NON_BLOCKING",
                ),
            ],
        },
        {
            "id": "team",
            "title_key": "tutorials.stages.team",
            "steps": [
                step(
                    "hire_engineer",
                    why_key="steps.hire_engineer.why",
                    requirement="ENGINEER_ACTIVE",
                    route="/employees",
                    target_id="hire-employee",
                    placement="left",
                    # 渐进式披露：第二位员工只提醒关键差异，不再逐字段讲解
                    metadata={
                        "ui_hints": [
                            {"target_id": "wizard-identity", "text_key": "hints.identity"},
                            {"target_id": "wizard-packages", "text_key": "hints.engineerAccess"},
                            {"target_id": "wizard-confirm", "text_key": "hints.confirmOnboard"},
                        ]
                    },
                ),
                step(
                    "configure_engineer",
                    why_key="steps.configure_engineer.why",
                    requirement="ENGINEER_READY",
                    route="/employees/{engineer_employee_id}",
                    target_id="employee-runtime-tab",
                    placement="top",
                    interaction_mode="NON_BLOCKING",
                ),
                step(
                    "hire_qa",
                    kind=OPTIONAL,
                    requirement="QA_ACTIVE",
                    route="/employees",
                    target_id="hire-employee",
                    placement="left",
                ),
            ],
        },
    ],
}

# 教程库：状态各自独立，Replay 只做信息回顾，不重跑业务门禁
TUTORIAL_LIBRARY = [
    {
        "id": "company-founding",
        "title_key": "tutorials.library.companyFounding",
        "route": "/",
        "replayable": True,
    },
    {
        "id": "first-project-practice",
        "title_key": "tutorials.library.firstProjectPractice",
        "route": "/projects",
        "replayable": True,
        "practice": True,
    },
]

# 兼容旧的 /tutorial/center 章节列表：章节仍然只是"去哪看"，不是进度门禁
TUTORIAL_CENTER = [
    {"id": "ceo-setup", "title_key": "tutorials.center.ceoSetup", "route": "/employees"},
    {
        "id": "employee-management",
        "title_key": "tutorials.center.employeeManagement",
        "route": "/employees",
    },
    {"id": "provider-setup", "title_key": "tutorials.center.providerSetup", "route": "/settings"},
    {"id": "cloud-docs", "title_key": "tutorials.center.cloudDocs", "route": "/drive"},
    {"id": "git", "title_key": "tutorials.center.git", "route": "/settings"},
    {"id": "projects", "title_key": "tutorials.center.projects", "route": "/projects"},
    {"id": "reviews", "title_key": "tutorials.center.reviews", "route": "/projects"},
    {"id": "delivery", "title_key": "tutorials.center.delivery", "route": "/projects"},
]
