"""Company-founding tutorial definition; it observes real business services."""

COMPANY_FOUNDING_TUTORIAL = {
    "id": "company-founding",
    "version": 1,
    "title": {"zh-CN": "建立你的 AI 公司", "en-US": "Found Your AI Company"},
    "stages": [
        {
            "id": "welcome",
            "title": {"zh-CN": "认识公司", "en-US": "Meet your company"},
            "steps": [
                {
                    "id": "company_setup",
                    "route": "/",
                    "target": "[data-tutorial='company-overview']",
                    "requirement": "COMPANY_CREATED",
                    "optional": False,
                },
            ],
        },
        {
            "id": "leadership",
            "title": {"zh-CN": "招募 CEO", "en-US": "Hire a CEO"},
            "steps": [
                {
                    "id": "hire_ceo",
                    "route": "/employees",
                    "requirement": "CEO_HIRED",
                    "optional": False,
                },
                {
                    "id": "configure_ceo",
                    "route": "/employees",
                    "requirement": "CEO_RUNTIME_CONFIGURED",
                    "optional": False,
                },
            ],
        },
        {
            "id": "company_systems",
            "title": {"zh-CN": "连接公司系统", "en-US": "Connect company systems"},
            "steps": [
                {
                    "id": "cloud_docs",
                    "route": "/drive",
                    "requirement": "COMPANY_DOCUMENT_CREATED",
                    "optional": False,
                },
                {
                    "id": "git_setup",
                    "route": "/settings#git",
                    "requirement": "GIT_CONFIGURED",
                    "optional": True,
                },
            ],
        },
        {
            "id": "team",
            "title": {"zh-CN": "组建团队", "en-US": "Build the team"},
            "steps": [
                {
                    "id": "hire_engineer",
                    "route": "/employees",
                    "requirement": "ENGINEER_HIRED",
                    "optional": False,
                },
                {
                    "id": "hire_qa",
                    "route": "/employees",
                    "requirement": "QA_HIRED",
                    "optional": True,
                },
            ],
        },
        {
            "id": "first_project",
            "title": {"zh-CN": "第一个项目", "en-US": "First project"},
            "steps": [
                {
                    "id": "create_project",
                    "route": "/projects",
                    "requirement": "FIRST_PROJECT_CREATED",
                    "optional": False,
                },
            ],
        },
        {
            "id": "reviews",
            "title": {"zh-CN": "人类评审门", "en-US": "Human review gates"},
            "steps": [
                {
                    "id": "requirements_review",
                    "route": "/projects",
                    "requirement": "REQUIREMENTS_APPROVED",
                    "optional": False,
                },
                {
                    "id": "design_review",
                    "route": "/projects",
                    "requirement": "DESIGN_APPROVED",
                    "optional": False,
                },
            ],
        },
        {
            "id": "production",
            "title": {"zh-CN": "开发与测试", "en-US": "Build and test"},
            "steps": [
                {
                    "id": "development",
                    "route": "/projects",
                    "requirement": "DEVELOPMENT_COMPLETED",
                    "optional": False,
                },
                {
                    "id": "testing",
                    "route": "/projects",
                    "requirement": "TESTING_COMPLETED",
                    "optional": False,
                },
                {
                    "id": "acceptance_review",
                    "route": "/projects",
                    "requirement": "ACCEPTANCE_APPROVED",
                    "optional": False,
                },
            ],
        },
        {
            "id": "delivery",
            "title": {"zh-CN": "交付并进入经营", "en-US": "Deliver and operate"},
            "steps": [
                {
                    "id": "delivery",
                    "route": "/projects",
                    "requirement": "DELIVERY_COMPLETED",
                    "optional": False,
                },
            ],
        },
    ],
}

TUTORIAL_CENTER = [
    {
        "id": "ceo-setup",
        "title": {"zh-CN": "CEO 设置", "en-US": "CEO Setup"},
        "route": "/employees",
    },
    {
        "id": "employee-management",
        "title": {"zh-CN": "员工管理", "en-US": "Employee Management"},
        "route": "/employees",
    },
    {
        "id": "provider-setup",
        "title": {"zh-CN": "Provider 设置", "en-US": "Provider Setup"},
        "route": "/settings#providers",
    },
    {
        "id": "cloud-docs",
        "title": {"zh-CN": "云文档", "en-US": "Cloud Documents"},
        "route": "/drive",
    },
    {
        "id": "git",
        "title": {"zh-CN": "Git 与仓库", "en-US": "Git & Repositories"},
        "route": "/settings#git",
    },
    {
        "id": "projects",
        "title": {"zh-CN": "项目", "en-US": "Projects"},
        "route": "/projects",
    },
    {
        "id": "reviews",
        "title": {"zh-CN": "真人评审", "en-US": "Human Reviews"},
        "route": "/projects",
    },
    {
        "id": "delivery",
        "title": {"zh-CN": "交付", "en-US": "Delivery"},
        "route": "/projects",
    },
]
