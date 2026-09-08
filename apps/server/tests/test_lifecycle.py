"""Employee lifecycle (v0.4): onboard / transfer / suspend / resume / offboard,
provisioning jobs + retry, naming, reconcile, audit, preview, seed migration."""

import uuid
from pathlib import Path

import pytest
from fake_gitea import FakeGiteaProvisioner
from sqlalchemy import select

from app.core.config import settings
from app.core.request_context import (
    RequestIdentity,
    reset_request_identity,
    set_request_identity,
)
from app.lifecycle.naming import naming
from app.lifecycle.provisioners.base import (
    Drift,
)
from app.lifecycle.provisioners.registry import get_registry
from app.models.lifecycle import AuditLog
from app.models.organization import Company, Department, Employee
from app.repositories import lifecycle as lifecycle_repo
from app.repositories import organization as org_repo
from app.repositories import providers as provider_repo
from app.repositories import runtimes as runtime_repo


def _departments(client) -> dict:
    company = client.get("/api/v1/company").json()
    return {d["slug"]: d["id"] for d in company["departments"]}


def _unique_slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _open_slot(client, department_id: int, note: str = "test establishment") -> int:
    """给某部门新开一个编制并返回 slot_id。

    用新开而不是复用已有坑：已有坑可能被创始员工占着，而部分唯一索引会让
    "往同一个坑塞第二个人"合法地失败 —— 那正是另一个用例要测的东西。
    """
    definitions = client.get("/api/v1/organizations/definitions").json()
    assert definitions, "职位模板应已由 seed / v12 建好"
    response = client.post(
        f"/api/v1/organizations/definitions/{definitions[0]['id']}/slots",
        json={"department_id": department_id, "note": note},
    )
    assert response.status_code == 201, response.text
    return response.json()[0]["id"]


def _onboard(client, slug, dept_slug="engineering", role="engineer"):
    response = client.post(
        "/api/v1/employees/onboard",
        json={
            "name": slug.replace("-", " ").title(),
            "slug": slug,
            "title": "Software Engineer",
            "role": role,
            "department_id": _departments(client)[dept_slug],
            "runtime_type": "mock",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture()
def gitea_down(monkeypatch):
    """Force the real gitea provisioner to report 'not running' deterministically."""
    provisioner = get_registry().provisioner_for("git:gitea")
    monkeypatch.setattr(provisioner, "_builtin_status", lambda: "stopped")
    return provisioner


# ---- onboarding ----


def test_onboard_slug_taken_by_another_company_is_409_not_500(client, db):
    """跨公司同名必须 409。

    `employees.slug` 和由它派生的 username / workspace_path / memory_namespace 都是
    **全局唯一**列，但存在性检查看起来是"按公司"的 —— 修复前跨公司撞名会一路走到
    INSERT，抛 `UNIQUE constraint failed: employees.memory_namespace` 变成 500
    （真机端到端验证时撞到）。这里把契约钉住：冲突要在 API 边界上说清楚。
    """
    slug = _unique_slug("crosscorp")
    _onboard(client, slug)

    other = Company(name="Second Co", slug=f"second-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    department = Department(company_id=other.id, name="Engineering", slug=f"eng-{other.id}")
    db.add(department)
    db.commit()

    token = set_request_identity(
        RequestIdentity(user_id=1, company_id=other.id, membership_role="OWNER", session_id=1)
    )
    try:
        response = client.post(
            "/api/v1/employees/onboard",
            json={
                "name": "Cross Company Dup",
                "slug": slug,
                "title": "Engineer",
                "role": "engineer",
                "department_id": department.id,
                "runtime_type": "mock",
            },
        )
        assert response.status_code == 409, f"{response.status_code}: {response.text[:200]}"
        assert "already exists" in response.text
    finally:
        reset_request_identity(token)


def test_onboard_slug_check_is_global_not_company_scoped(db):
    """仓库层的唯一性口径要单独钉住：`get_employee_by_slug` 不能用来判唯一性。"""
    other = Company(name="Third Co", slug=f"third-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    taken = db.scalar(select(Employee.id)) is not None
    token = set_request_identity(
        RequestIdentity(user_id=1, company_id=other.id, membership_role="OWNER", session_id=1)
    )
    try:
        existing_slug = db.scalar(select(Employee.slug))
        assert org_repo.get_employee_by_slug(db, existing_slug) is None, (
            "前提变了：get_employee_by_slug 不再按公司过滤，这个测试要一起重写"
        )
        assert org_repo.slug_taken_anywhere(db, existing_slug) is (
            taken and existing_slug is not None
        )
    finally:
        reset_request_identity(token)


def test_onboard_done_with_gitea_skipped(client, gitea_down):
    """gitea 未安装 → git 步骤 skipped（不是 failed），job 照常 done。

    入职/教程不再被可选资源堵死；git 账户留下 provisioning_state=skipped 的
    透明痕迹，装上 gitea 后可对 job 重跑或走职位层收敛重新开通。
    """
    body = _onboard(client, _unique_slug("onb"))
    employee = body["employee"]
    job = body["job"]
    assert employee["lifecycle_status"] == "active"  # 不再停在 onboarding
    assert employee["username"] == employee["slug"]
    assert job["kind"] == "onboarding"
    assert job["status"] == "done"
    by_action_resource = {(s["action"], s["resource_type"]): s for s in job["steps"]}
    assert by_action_resource[("provision", "workspace")]["status"] == "done"
    assert by_action_resource[("provision", "docs")]["status"] == "done"
    git_provision = by_action_resource[("provision", "git")]
    assert git_provision["status"] == "skipped"
    assert "gitea" in git_provision["error"]
    assert job["done_steps"] == job["total_steps"]
    accounts = client.get(f"/api/v1/employees/{employee['id']}/accounts").json()
    status_by_type = {a["resource_type"]: a["provisioning_state"] for a in accounts}
    assert status_by_type["workspace"] == "done"
    assert status_by_type["docs"] == "done"
    # git 账户状态仍是 provisioning（未开通）；skipped 的痕迹在 job 步骤上可见


def test_onboard_persists_provider_model_and_brain(client, db, gitea_down):
    slug = _unique_slug("guided")
    response = client.post(
        "/api/v1/employees/onboard",
        json={
            "name": "Guided CEO",
            "slug": slug,
            "title": "Chief Executive Officer",
            "role": "ceo",
            "department_id": _departments(client)["executive"],
            "runtime_type": "mock",
            "provider_name": "CEO OpenAI",
            "provider_type": "openai",
            "provider_api_key": "sk-guided-secret",
            "model": "gpt-5.2",
            "personality": "Decisive and strategic",
            "goals": "Deliver durable customer value",
            "learning_enabled": True,
            "curiosity": 0.8,
        },
    )
    assert response.status_code == 201, response.text
    employee_id = response.json()["employee"]["id"]

    brain = runtime_repo.get_brain(db, employee_id)
    assert brain is not None
    assert brain.personality == "Decisive and strategic"
    assert brain.goals == "Deliver durable customer value"
    assert brain.learning_policy["enabled"] is True
    assert brain.curiosity == 0.8

    binding = provider_repo.get_primary_binding(db, employee_id)
    assert binding is not None
    assert binding.model == "gpt-5.2"
    instance = runtime_repo.get_instance_for_employee(db, employee_id)
    assert instance is not None
    assert instance.model_binding_id == binding.id
    provider = provider_repo.get_provider(db, binding.provider_id)
    assert provider is not None
    assert provider.owner_employee_id == employee_id


def test_retry_reruns_only_failed_steps(client, gitea_down, monkeypatch):
    """重试只回放 failed 步骤；done 步骤的 attempts 不动。用真实失败（docs 抛错）造 partial。"""
    from app.lifecycle.provisioners.base import ProvisionerError
    from app.lifecycle.provisioners.registry import get_registry

    original = get_registry()._provisioners["docs:builtin"]

    class _Flaky(original.__class__):
        async def provision_employee(self, employee, entitlement, ctx):
            raise ProvisionerError("docs flaky but retryable")

    # 先带着坏 docs 入职 → job partial（workspace done、docs failed、git skipped）
    monkeypatch.setitem(get_registry()._provisioners, "docs:builtin", _Flaky())
    body = _onboard(client, _unique_slug("retry"))
    assert body["job"]["status"] == "partial"
    offline = body["job"]
    failed = [s for s in offline["steps"] if s["status"] == "failed"]
    assert failed and all(s["error"] for s in failed)
    employee = client.get(f"/api/v1/employees/{body['employee']['id']}").json()
    assert employee["lifecycle_status"] == "onboarding"  # partial 时保持 onboarding

    # 修复后重试：done 步骤不被重跑，failed → done/skipped 照常
    monkeypatch.setitem(get_registry()._provisioners, "docs:builtin", original)
    done_attempts = {s["id"]: s["attempts"] for s in offline["steps"] if s["status"] == "done"}
    retried = client.post(f"/api/v1/provisioning-jobs/{body['job']['id']}/retry").json()
    assert retried["status"] == "done"
    for step in retried["steps"]:
        if step["id"] in done_attempts:
            assert step["attempts"] == done_attempts[step["id"]]  # done 步骤不被重跑
    assert all(s["status"] in ("done", "skipped") for s in retried["steps"])


def test_onboard_active_with_fake_gitea(client, fake_gitea):
    body = _onboard(client, _unique_slug("full"))
    assert body["job"]["status"] == "done"
    assert body["job"]["done_steps"] == body["job"]["total_steps"]
    assert body["employee"]["lifecycle_status"] == "active"
    assert body["employee"]["slug"] in fake_gitea.users

    entitlements = client.get(f"/api/v1/employees/{body['employee']['id']}/entitlements").json()
    keys = {e["entitlement"]["key"] for e in entitlements}
    assert {
        "workspace:private",
        "workspace:dev",
        "docs:company-read",
        "docs:engineering",
        "git:company-org-member",
        "git:engineering-team",
    } <= keys
    base = next(e for e in entitlements if e["entitlement"]["key"] == "workspace:private")
    assert any(s["package_name"] == "Base Employee" for s in base["sources"])


def test_onboard_does_not_block_on_missing_gitea(client, gitea_down):
    """入职不再依赖 git 健康：gitea 未装 → 直接 done/active；无需等安装再重试。"""
    body = _onboard(client, _unique_slug("late"))
    assert body["job"]["status"] == "done"
    employee = client.get(f"/api/v1/employees/{body['employee']['id']}").json()
    assert employee["lifecycle_status"] == "active"


# ---- transfer ----


def test_transfer_diff_and_employment_history(client, fake_gitea):
    body = _onboard(client, _unique_slug("xfer"))
    employee_id = body["employee"]["id"]
    research_id = _departments(client)["research"]

    job = client.post(
        f"/api/v1/employees/{employee_id}/transfer",
        json={"department_id": research_id, "reason": "reorg"},
    ).json()
    assert job["kind"] == "transfer"
    assert job["status"] == "done"
    descriptions = {s["description"] for s in job["steps"]}
    assert any(d.startswith("ADD docs:research") for d in descriptions)
    assert any(d.startswith("ADD git:research-team") for d in descriptions)
    assert any(d.startswith("REMOVE docs:engineering") for d in descriptions)
    assert any(d.startswith("REMOVE git:engineering-team") for d in descriptions)
    assert any(d.startswith("KEEP workspace:private") for d in descriptions)

    # P4b 契约（拍板）：只搬部门、没有编制承接 ⇒ **不创建无坑 PRIMARY**。
    # 人还在、权限已换、履历里没有一条"职位"—— 状态派生成 AVAILABLE。
    # 这条断言取代了旧期望"transfer 必然留下 2 行任职"：旧写法正是 14 条
    # 无编制主职的生产方式，所以它是被裁定换掉的，不是被放宽的。
    employment = client.get(f"/api/v1/employees/{employee_id}/employment").json()
    assert employment["history"] == []
    assert employment["current"] is None
    # 单人派生视图挂在名册详情上（`GET /employees/{id}` 的字段并入等 employees.py 的
    # WIP 落地后一起做，见 position-system.md §5 的落地说明）
    derived = client.get(f"/api/v1/talent-roster/{employee_id}").json()
    assert derived["workforce_status"] == "available"
    assert derived["current_position"] is None
    assert derived["has_primary_assignment"] is False

    # 显式分配工作流：给出坑才有 PRIMARY 任职
    first_slot = _open_slot(client, research_id, note="appoint researcher")
    assigned = client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments",
        json={"slot_id": first_slot, "reason": "appoint"},
    )
    assert assigned.status_code == 201, assigned.text
    assert assigned.json()["position_slot_id"] == first_slot
    assert assigned.json()["occupied_slot"] is True

    # 再转岗到另一个坑：关旧行 + 开新行，旧行状态字保持 v0.4 契约的 "transferred"
    second_slot = _open_slot(client, research_id, note="next establishment")
    moved = client.post(
        f"/api/v1/employees/{employee_id}/transfer",
        json={"department_id": research_id, "slot_id": second_slot, "reason": "reorg 2"},
    )
    assert moved.status_code == 200, moved.text
    history = client.get(f"/api/v1/employees/{employee_id}/employment").json()
    assert len(history["history"]) == 2
    old, new_row = history["history"]
    assert old["effective_to"] is not None
    assert old["employment_status"] == "transferred"
    assert new_row["effective_to"] is None
    assert new_row["department_id"] == research_id
    assert history["current"]["id"] == new_row["id"]
    # 坑的占用态随转岗迁移：旧坑回到 vacant，新坑 occupied（都是派生值）
    assert (
        client.get(f"/api/v1/organizations/slots/{first_slot}").json()["occupancy_status"]
        == "vacant"
    )
    assert (
        client.get(f"/api/v1/organizations/slots/{second_slot}").json()["occupancy_status"]
        == "occupied"
    )

    employee = client.get(f"/api/v1/employees/{employee_id}").json()
    assert employee["lifecycle_status"] == "active"
    assert employee["department_id"] == research_id

    # no duplicate entitlements after re-running the same transfer
    again = client.post(
        f"/api/v1/employees/{employee_id}/transfer", json={"department_id": research_id}
    ).json()
    assert again["status"] == "done"
    assert all(s["action"] == "keep" for s in again["steps"])
    entitlements = client.get(f"/api/v1/employees/{employee_id}/entitlements").json()
    keys = [e["entitlement"]["key"] for e in entitlements]
    assert len(keys) == len(set(keys))
    assert "docs:research" in keys and "docs:engineering" not in keys


# ---- suspend / resume ----


def test_suspend_resume_cycle(client, fake_gitea, db):
    body = _onboard(client, _unique_slug("susp"))
    employee_id = body["employee"]["id"]

    job = client.post(
        f"/api/v1/employees/{employee_id}/suspend", json={"reason": "gardening leave"}
    ).json()
    assert job["kind"] == "suspension"
    assert job["status"] == "done"
    employee = client.get(f"/api/v1/employees/{employee_id}").json()
    assert employee["lifecycle_status"] == "suspended"
    accounts = client.get(f"/api/v1/employees/{employee_id}/accounts").json()
    assert accounts and all(a["status"] == "suspended" for a in accounts)
    # runtime stopped, workspace preserved
    instance = runtime_repo.get_instance_for_employee(db, employee_id)
    assert instance.status == "stopped"
    assert Path(employee["workspace_path"]).exists()

    resumed = client.post(f"/api/v1/employees/{employee_id}/resume").json()
    assert resumed["kind"] == "resumption"
    assert resumed["status"] == "done"
    employee = client.get(f"/api/v1/employees/{employee_id}").json()
    assert employee["lifecycle_status"] == "active"
    accounts = client.get(f"/api/v1/employees/{employee_id}/accounts").json()
    assert all(a["status"] == "active" for a in accounts)
    db.expire_all()
    instance = runtime_repo.get_instance_for_employee(db, employee_id)
    assert instance.status == "running"


def test_suspend_rejects_offboarded(client, fake_gitea):
    body = _onboard(client, _unique_slug("guard"))
    employee_id = body["employee"]["id"]
    client.post(f"/api/v1/employees/{employee_id}/offboard", json={})
    response = client.post(f"/api/v1/employees/{employee_id}/suspend", json={})
    assert response.status_code == 409


# ---- offboard ----


def test_offboard_transfers_assets_and_archives(client, fake_gitea):
    slug = _unique_slug("offb")
    body = _onboard(client, slug)
    employee_id = body["employee"]["id"]
    dept_id = body["employee"]["department_id"]

    # 先经分配工作流拿到一个编制 —— 这样才能验证"离职交还坑"这条新规则
    slot_id = _open_slot(client, dept_id, note="offboard fixture establishment")
    client.post(
        f"/api/v1/talent-roster/{employee_id}/assignments",
        json={"slot_id": slot_id, "reason": "hire in"},
    )
    assert (
        client.get(f"/api/v1/organizations/slots/{slot_id}").json()["occupancy_status"]
        == "occupied"
    )

    job = client.post(f"/api/v1/employees/{employee_id}/offboard", json={"reason": "left"}).json()
    assert job["kind"] == "offboarding"
    assert job["status"] == "done"

    # P4b：离职必须把编制还回去。不关窗的话这个坑会被一个已离开的人永久占住
    # —— dev 库里现在就有一个 offboarded 的人挂在生效主职上。
    slot_after = client.get(f"/api/v1/organizations/slots/{slot_id}").json()
    assert slot_after["occupancy_status"] == "vacant", slot_after

    employee = client.get(f"/api/v1/employees/{employee_id}").json()
    assert employee["lifecycle_status"] == "offboarded"

    accounts = client.get(f"/api/v1/employees/{employee_id}/accounts").json()
    assert accounts and all(a["status"] == "deprovisioned" for a in accounts)

    # v1 default: assets → department (owner NULL + department in metadata)
    # assets are no longer owned by the employee
    assert client.get(f"/api/v1/employees/{employee_id}/assets").json() == []

    # workspace archived
    archives = list((Path(settings.data_root) / "archive").glob(f"{slug}-*.tar.gz"))
    assert archives, "expected workspace archive tar in data/archive/"

    # employee row + employment history survive
    assert employee["slug"] == slug
    employment = client.get(f"/api/v1/employees/{employee_id}/employment").json()
    # 历史存活：关窗不是删行，离职之后仍能查到他任过什么职
    assert len(employment["history"]) == 1
    assert employment["history"][0]["effective_to"] is not None
    assert employment["current"] is None

    # timeline shows the lifecycle events
    timeline = client.get(f"/api/v1/employees/{employee_id}/timeline").json()
    types = {e["type"] for e in timeline}
    assert "employee.hired" in types
    assert "employee.offboarded" in types
    assert "asset.transferred" in types

    # hard delete is forbidden by default
    assert client.delete(f"/api/v1/employees/{employee_id}").status_code == 403
    assert dept_id  # silence unused


def test_offboard_to_another_employee(client, fake_gitea, db):
    source = _onboard(client, _unique_slug("src"))["employee"]
    target = _onboard(client, _unique_slug("dst"))["employee"]

    job = client.post(
        f"/api/v1/employees/{source['id']}/offboard",
        json={"transfer_to": str(target["id"])},
    ).json()
    assert job["status"] == "done"
    assets = lifecycle_repo.list_assets(db, owner_employee_id=target["id"])
    transferred = [
        a for a in assets if (a.metadata_json or {}).get("transferred_from") == source["id"]
    ]
    assert transferred, "expected source assets re-owned by target employee"


# ---- naming ----


def test_naming_policy_is_deterministic():
    assert naming.username("Jane  Doe") == "jane-doe"
    assert naming.username("Jane  Doe") == naming.username("Jane  Doe")
    assert naming.gitea_username("jane-doe") == "jane-doe"
    assert naming.gitea_email("jane-doe") == "jane-doe@eidolon.local"
    assert naming.personal_docs_path("jane-doe") == "drive/knowledge/personal/jane-doe"


# ---- reconcile ----


def test_reconcile_reports_drift(client, fake_gitea, monkeypatch):
    body = _onboard(client, _unique_slug("reco"))
    employee_id = body["employee"]["id"]

    clean = client.post(f"/api/v1/employees/{employee_id}/reconcile").json()
    assert clean == {"drifts": []}

    workspace = get_registry().provisioner_for("workspace:local")

    async def fake_reconcile(account, ctx):
        if account.resource_type == "workspace":
            return [
                Drift(
                    account_id=account.id,
                    resource_type="workspace",
                    kind="missing",
                    detail="workspace directory missing",
                )
            ]
        return []

    monkeypatch.setattr(workspace, "reconcile", fake_reconcile)
    result = client.post(f"/api/v1/employees/{employee_id}/reconcile").json()
    assert result["drifts"] == [
        {
            "account_id": result["drifts"][0]["account_id"],
            "resource_type": "workspace",
            "kind": "missing",
            "detail": "workspace directory missing",
        }
    ]


# ---- audit ----


def test_audit_trail_records_before_after(client, fake_gitea, db):
    body = _onboard(client, _unique_slug("audit"))
    employee_id = body["employee"]["id"]
    client.post(f"/api/v1/employees/{employee_id}/suspend", json={"reason": "audit check"})

    rows = list(
        db.scalars(
            select(AuditLog).where(AuditLog.employee_id == employee_id).order_by(AuditLog.id)
        )
    )
    actions = [r.action for r in rows]
    assert "employee.hired" in actions
    assert "employee.suspend" in actions
    hired = next(r for r in rows if r.action == "employee.hired")
    assert hired.actor == "user"
    assert hired.before_json is None
    assert hired.after_json["lifecycle_status"] == "onboarding"
    suspended = next(r for r in rows if r.action == "employee.suspend")
    assert suspended.reason == "audit check"
    assert suspended.before_json["lifecycle_status"] == "active"
    assert suspended.after_json["lifecycle_status"] == "suspended"


# ---- preview ----


def test_preview_computes_plan_without_writes(client, gitea_down, db):
    departments = _departments(client)
    employees_before = len(client.get("/api/v1/employees").json())
    jobs_before = len(client.get("/api/v1/provisioning-jobs").json())

    result = client.post(
        "/api/v1/provisioning/preview", json={"department_id": departments["engineering"]}
    ).json()
    assert result["steps"]
    by_resource = {}
    for step in result["steps"]:
        by_resource.setdefault(step["resource_type"], step)
    assert by_resource["workspace"]["available"] is True
    assert by_resource["docs"]["available"] is True
    assert by_resource["git"]["available"] is False  # gitea not running → wizard can warn

    assert len(client.get("/api/v1/employees").json()) == employees_before
    assert len(client.get("/api/v1/provisioning-jobs").json()) == jobs_before


# ---- seed / legacy migration (§12) ----


def test_access_packages_seeded(client):
    packages = client.get("/api/v1/access-packages").json()
    # 只对**内置那六个**取等，不对整个列表取等：权限包是可再生资源
    # （P4d 的职位包、用户自建包都会进这个列表）。全库相等断言在共享测试库里
    # 迟早被别的测试造出的包打破 —— P4d 就打破过一次，而且是"合跑红、单跑绿"。
    seeded = {p["slug"] for p in packages if p["built_in"]}
    assert seeded == {
        "base-employee",
        "ceo",
        "product-manager",
        "researcher",
        "engineer",
        "qa-engineer",
    }
    engineer = next(p for p in packages if p["slug"] == "engineer")
    engineer_keys = {e["key"] for e in engineer["entitlements"]}
    assert engineer_keys == {"git:engineering-team", "docs:engineering", "workspace:dev"}


def test_legacy_employees_backfilled(client, db):
    employees = {e["slug"]: e for e in client.get("/api/v1/employees").json()}
    alice = employees["alice"]
    assert alice["lifecycle_status"] == "active"
    assert alice["username"] == "alice"

    employment = client.get(f"/api/v1/employees/{alice['id']}/employment").json()
    assert employment["current"] is not None
    assert employment["current"]["employment_status"] == "active"

    accounts = client.get(f"/api/v1/employees/{alice['id']}/accounts").json()
    status_by_type = {a["resource_type"]: a["status"] for a in accounts}
    assert status_by_type["workspace"] == "active"
    assert status_by_type["docs"] == "active"

    entitlements = client.get(f"/api/v1/employees/{alice['id']}/entitlements").json()
    keys = {e["entitlement"]["key"] for e in entitlements}
    assert {"workspace:private", "docs:company-read", "git:company-org-member"} <= keys

    # v0.4 的 `/positions`（部门头衔）与新域的 `/organizations/definitions`（职位定义）
    # 是两个不同端点：这条 §12 契约测的是前者，别把它顺手改成新路径。
    positions = client.get("/api/v1/positions").json()
    titles = {p["title"] for p in positions}
    assert {"CEO", "Product Manager", "Researcher", "Engineer", "QA Engineer"} <= titles


def test_seed_lifecycle_is_idempotent(db):
    from app.services.lifecycle import seed_lifecycle

    before = len(lifecycle_repo.list_packages(db))
    seed_lifecycle(db)
    seed_lifecycle(db)
    assert len(lifecycle_repo.list_packages(db)) == before


# ---- compat: pre-v0.4 POST /employees routes through onboarding ----


def test_compat_create_employee_routes_through_engine(client, gitea_down):
    response = client.post(
        "/api/v1/employees",
        json={"name": f"Compat {_unique_slug('c')}", "role": "researcher"},
    )
    assert response.status_code == 201, response.text
    employee = response.json()
    assert employee["lifecycle_status"] == "active"  # gitea 未装 → skipped，不拦入职
    jobs = client.get(f"/api/v1/provisioning-jobs?employee_id={employee['id']}").json()
    assert len(jobs) == 1
    assert jobs[0]["kind"] == "onboarding"
