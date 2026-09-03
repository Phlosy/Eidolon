"""核心教程：由真实业务状态驱动，公司建立与项目实战已经拆成两个教程。

这一组测试刻意**不走**"点一下就完成"的路径 —— 每一步都通过真实业务 API
把状态推到满足门禁，再断言教程自己动了。反过来也测：没做真实动作时，
点 complete 必须被 409 挡回来。
"""

import uuid

from sqlalchemy import func, select

from app.models.drive import DriveNode
from app.models.event import Event
from app.models.lifecycle import ProvisioningJob
from app.models.project import Artifact, Project, Task, WorkSession
from app.models.runtime import RuntimeInstance
from app.services import tutorial as tutorial_service


def _new_founder(client, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "long enough password1"}
    )
    token = response.json()["development_verification_token"]
    return client.post("/api/v1/auth/verify-email", json={"token": token}).json()


def _packages(client) -> dict[str, int]:
    return {item["slug"]: item["id"] for item in client.get("/api/v1/access-packages").json()}


def _hire(
    client,
    founder: dict,
    *,
    name: str,
    role: str,
    dept_slug: str,
    package: str,
    provider: bool = True,
) -> dict:
    """走真实 Hire Employee Wizard 背后的端点，返回 onboard 响应。"""
    departments = {item["slug"]: item["id"] for item in founder["company"]["departments"]}
    slug = f"{name.lower().replace(' ', '-')}-{uuid.uuid4().hex[:6]}"
    body = {
        "name": name,
        "slug": slug,
        "title": name,
        "role": role,
        "department_id": departments[dept_slug],
        "runtime_type": "mock",
        "access_package_ids": [_packages(client)[package]],
    }
    if provider:
        body |= {
            "provider_name": f"{role}-provider",
            "provider_type": "custom",
            "provider_base_url": "https://api.example.com/v1",
            "provider_api_key": "sk-test-key-enough-length",
            "model": "test-model",
        }
    response = client.post("/api/v1/employees/onboard", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _step(client) -> str:
    return client.get("/api/v1/tutorial").json()["current_step"]


def _business_rows(db) -> dict[str, int]:
    """数一遍"真跑了项目会增长"的表 —— 零成本跳过必须一行都不多。"""
    tables = (Project, Task, Artifact, WorkSession, RuntimeInstance, DriveNode, ProvisioningJob)
    return {table.__name__: db.scalar(select(func.count()).select_from(table)) for table in tables}


# ---------------------------------------------------------------- 定义与进度


def test_core_definition_declares_kinds_targets_and_modes(client):
    _new_founder(client, "tutorial-def@example.com")
    definition = client.get("/api/v1/tutorial/definition").json()
    assert definition["id"] == "company-founding"
    steps = tutorial_service._steps("company-founding")

    for item in steps:
        assert item["kind"] in {"REQUIRED_ACTION", "OPTIONAL_ACTION", "INFORMATION"}
        assert item["target_id"], f"{item['id']} 没有 target_id，聚光灯无处可打"
        assert item["route"].startswith("/"), f"{item['id']} 没有跨路由目标"
        assert item["interaction_mode"] in {"FOCUS_ONLY", "TARGET_ONLY", "NON_BLOCKING"}
        assert item["requirement"], "每一步都必须绑一个真实业务门"
        # 必做步骤不可跳过是类型层的性质，不是每个定义里手写
        assert item["allow_skip"] == (item["kind"] != "REQUIRED_ACTION")

    skippable = {item["id"] for item in steps if item["allow_skip"]}
    assert skippable == {"company_setup", "git_setup", "hire_qa"}
    # Classic Snake 已经不在核心教程里了
    assert not any("project" in item["id"] or "delivery" in item["id"] for item in steps)


def test_progress_is_per_tutorial_and_persisted(client):
    _new_founder(client, "tutorial-progress@example.com")
    started = client.post("/api/v1/tutorial/start").json()
    assert started["tutorial_id"] == "company-founding"
    assert started["current_step"] == "company_setup"

    assert client.post("/api/v1/tutorial/pause").json()["status"] == "paused"
    resumed = client.post("/api/v1/tutorial/resume").json()
    assert resumed["status"] == "active" and resumed["current_step"] == "company_setup"

    # 实战教程是另一行进度，初始未开始，且不因为读了核心就被创建
    practice = client.get("/api/v1/practice").json()["progress"]
    assert practice["tutorial_id"] == "first-project-practice"
    assert practice["status"] == "not_started"


# ---------------------------------------------------------------- 不能假完成


def test_required_steps_cannot_be_faked_by_clicking(client):
    _new_founder(client, "tutorial-fake@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")

    for step in ("hire_ceo", "configure_ceo_runtime", "cloud_docs", "hire_engineer"):
        denied = client.post(f"/api/v1/tutorial/steps/{step}/complete")
        assert denied.status_code == 409, step
        assert "real business action" in denied.json()["detail"]
    assert _step(client) == "hire_ceo"


def test_optional_steps_can_be_skipped_but_required_cannot(client):
    founder = _new_founder(client, "tutorial-skip@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")

    blocked = client.post("/api/v1/tutorial/steps/hire_ceo/skip")
    assert blocked.status_code == 409 and "REQUIRED_ACTION" in blocked.json()["detail"]

    # git_setup 只有在走到它时才允许跳过
    _hire(client, founder, name="Tutorial CEO", role="ceo", dept_slug="executive", package="ceo")
    assert _step(client) != "company_setup"


def test_incomplete_onboarding_does_not_complete_the_ceo_step(client):
    """只建了员工记录、onboarding 还没完成 → 教程不许说"CEO 已入职"。"""
    founder = _new_founder(client, "tutorial-partial@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")
    departments = {item["slug"]: item["id"] for item in founder["company"]["departments"]}

    half = client.post(
        "/api/v1/employees",
        json={
            "name": "Stuck CEO",
            "slug": f"stuck-ceo-{uuid.uuid4().hex[:6]}",
            "role": "ceo",
            "title": "CEO",
            "department_id": departments["executive"],
            "runtime_type": "mock",
        },
    )
    assert half.status_code == 201
    assert half.json()["lifecycle_status"] == "onboarding"
    assert _step(client) == "hire_ceo", "半完成的入职不能推进教程"


# ---------------------------------------------------------------- 真实推进


def test_core_tutorial_completes_through_real_actions_only(client, fake_gitea):
    founder = _new_founder(client, "tutorial-core@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")

    _hire(client, founder, name="Core CEO", role="ceo", dept_slug="executive", package="ceo")
    # 一次真实入职同时兑现了 runtime / provider / 资源三类门禁
    assert _step(client) == "cloud_docs"

    uploaded = client.post(
        "/api/v1/drive/files?zone=knowledge&name=welcome.md",
        content=b"# Welcome",
        headers={"Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert _step(client) == "git_setup"

    # git 没配 → 显式跳过这个可选步骤，而不是让它悄悄溜过去
    client.post("/api/v1/tutorial/steps/git_setup/skip")
    assert _step(client) == "hire_engineer"

    _hire(
        client,
        founder,
        name="Core Engineer",
        role="engineer",
        dept_slug="engineering",
        package="engineer",
    )
    assert _step(client) == "hire_qa"

    progress = client.post("/api/v1/tutorial/steps/hire_qa/skip").json()
    assert progress["status"] == "completed" and progress["current_step"] == "completed"

    # 公司状态通过 API 断言：请求上下文才会解析到"这个用户"的公司
    stage = client.get("/api/v1/company").json()["stage"]
    assert stage == "OPERATING", "核心教程通关才切 OPERATING"
    assert client.get("/api/v1/auth/me").json()["user"]["onboarding_status"] == "completed"
    # 实战教程没有被核心完成牵连
    assert client.get("/api/v1/practice").json()["progress"]["status"] == "not_started"


def test_engineer_without_provider_leaves_the_tutorial_open(client, fake_gitea):
    """ENGINEER_READY 需要真实 provider 绑定；少了它教程不能宣称完成。"""
    founder = _new_founder(client, "tutorial-noprovider@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")
    _hire(client, founder, name="NP CEO", role="ceo", dept_slug="executive", package="ceo")
    client.post(
        "/api/v1/drive/files?zone=knowledge&name=n.md",
        content=b"# n",
        headers={"Content-Type": "text/markdown"},
    )
    client.post("/api/v1/tutorial/steps/git_setup/skip")

    _hire(
        client,
        founder,
        name="NP Engineer",
        role="engineer",
        dept_slug="engineering",
        package="engineer",
        provider=False,
    )
    step = _step(client)
    assert step in {"configure_engineer", "hire_engineer"}
    assert client.get("/api/v1/tutorial").json()["status"] == "active"
    assert client.get("/api/v1/tutorial").json()["completed_steps"].count("configure_engineer") == 0


# ---------------------------------------------------------------- 实战教程


def test_practice_skip_is_zero_cost(client, db):
    """跳过 First Project Practice：不许建项目、不许调 Agent、不许起 runtime。"""
    _new_founder(client, "tutorial-zero-cost@example.com")
    before = _business_rows(db)

    response = client.post("/api/v1/practice/skip")
    assert response.status_code == 200
    progress = response.json()
    assert progress["status"] == "skipped"
    assert progress["tutorial_id"] == "first-project-practice"

    assert _business_rows(db) == before, "跳过实战产生了业务副作用"

    # 核心教程状态不受影响
    assert client.get("/api/v1/tutorial").json()["status"] == "not_started"


def test_practice_can_be_started_later_from_the_center(client):
    _new_founder(client, "tutorial-later@example.com")
    client.post("/api/v1/practice/skip")
    assert client.get("/api/v1/practice").json()["progress"]["status"] == "skipped"

    started = client.post("/api/v1/practice/start").json()
    assert started["status"] == "active"
    assert started["current_step"] == "create_project"


def test_practice_preview_reports_real_cost_before_starting(client, fake_gitea, db):
    founder = _new_founder(client, "tutorial-preview@example.com")
    client.post("/api/v1/practice/start")
    _hire(client, founder, name="PV CEO", role="ceo", dept_slug="executive", package="ceo")

    preview = client.get("/api/v1/practice/preview").json()
    assert preview["tutorial_accelerated"] is True
    assert preview["template_name"] == "Classic Snake"
    # mock runtime 不会真的调用模型 → 不该谎报 token 成本
    assert {member["role"] for member in preview["team"]} == {"ceo"}
    assert preview["team"][0]["model"] == "test-model"
    assert preview["uses_llm"] is False and preview["mock_only"] is True

    # 换成真实 runtime 后，成本警告必须亮起来
    ceo_id = preview["team"][0]["employee_id"]
    ceo_runtime = db.scalar(select(RuntimeInstance).where(RuntimeInstance.employee_id == ceo_id))
    assert ceo_runtime is not None
    ceo_runtime.runtime_type = "claude_code"
    db.commit()
    db.expire_all()
    costly = client.get("/api/v1/practice/preview").json()
    assert costly["uses_llm"] is True and costly["mock_only"] is False
    # preview 只读，不推进
    assert client.get("/api/v1/practice").json()["progress"]["current_step"] == "create_project"


def test_practice_steps_are_gated_the_same_way(client):
    _new_founder(client, "tutorial-practice-gate@example.com")
    client.post("/api/v1/practice/start")
    denied = client.post("/api/v1/tutorial/steps/create_project/complete")
    assert denied.status_code == 409
    assert client.post("/api/v1/tutorial/steps/create_project/skip").status_code == 409


# ---------------------------------------------------------------- 基础设施


def test_unknown_requirement_is_false_not_a_500():
    # 声明合法性在导入期就炸，而不是让某个门永远卡在运行时
    problems = tutorial_service._PROBLEMS
    assert problems == [], f"教程声明与注册表不一致：{problems}"
    from app.tutorials.requirements import evaluate

    assert evaluate("NO_SUCH_REQUIREMENT", None) is False


def test_legacy_defer_qa_endpoint_still_works(client, fake_gitea):
    founder = _new_founder(client, "tutorial-legacy@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")
    _hire(client, founder, name="LG CEO", role="ceo", dept_slug="executive", package="ceo")
    client.post(
        "/api/v1/drive/files?zone=knowledge&name=l.md",
        content=b"# l",
        headers={"Content-Type": "text/markdown"},
    )
    client.post("/api/v1/tutorial/steps/git_setup/skip")
    _hire(
        client,
        founder,
        name="LG Engineer",
        role="engineer",
        dept_slug="engineering",
        package="engineer",
    )

    deferred = client.post("/api/v1/tutorial/defer-qa").json()
    assert "hire_qa" in deferred["skipped_steps"]
    assert deferred["status"] == "completed"


def test_library_lists_both_tutorials_with_progress(client):
    _new_founder(client, "tutorial-library@example.com")
    client.post("/api/v1/practice/skip")
    library = client.get("/api/v1/tutorial/library").json()

    ids = {item["definition"]["id"] for item in library["tutorials"]}
    assert ids == {"company-founding", "first-project-practice"}
    by_id = {item["definition"]["id"]: item["progress"] for item in library["tutorials"]}
    assert by_id["first-project-practice"]["status"] == "skipped"
    listed = {entry["id"] for entry in library["library"]}
    assert {"company-founding", "first-project-practice"} <= listed


def test_advancement_publishes_tutorial_events(client, fake_gitea, db):

    founder = _new_founder(client, "tutorial-events@example.com")
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")
    _hire(client, founder, name="EV CEO", role="ceo", dept_slug="executive", package="ceo")

    def tutorial_events() -> list[str]:
        return [
            row.type for row in db.scalars(select(Event).where(Event.type.like("tutorial.%")))
        ]

    types = tutorial_events()
    assert "tutorial.advanced" in types, f"没有发出推进事件：{types}"

    # 事件不得自我放大：状态稳定之后，重复读取进度不能再刷事件。
    # （先读一次让异步 provisioning 落定 —— 那期间的推进是真实状态变化，不算自激。）
    client.get("/api/v1/tutorial")
    settled = len(tutorial_events())
    for _ in range(3):
        client.get("/api/v1/tutorial")
    assert len(tutorial_events()) == settled, "稳定状态下读取进度仍在发事件（会自激）"
