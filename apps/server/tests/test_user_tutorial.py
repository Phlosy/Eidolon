"""The guided company tutorial is user-scoped and domain-state driven."""


def _new_founder(client, email: str) -> dict:
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "long enough password1"}
    )
    token = response.json()["development_verification_token"]
    return client.post("/api/v1/auth/verify-email", json={"token": token}).json()


def test_tutorial_definition_and_persistent_user_progress(client):
    _new_founder(client, "tutorial@example.com")
    definition = client.get("/api/v1/tutorial/definition")
    assert definition.status_code == 200
    assert definition.json()["id"] == "company-founding"
    assert len(definition.json()["stages"]) >= 6

    started = client.post("/api/v1/tutorial/start").json()
    assert started["current_stage"] == "welcome"
    assert started["current_step"] == "company_setup"

    paused = client.post("/api/v1/tutorial/pause").json()
    assert paused["status"] == "paused"
    resumed = client.post("/api/v1/tutorial/resume").json()
    assert resumed["status"] == "active"
    assert resumed["current_step"] == "company_setup"


def test_tutorial_cannot_fake_domain_completion(client):
    _new_founder(client, "real-state@example.com")
    client.post("/api/v1/tutorial/start")
    denied = client.post("/api/v1/tutorial/steps/hire_ceo/complete")
    assert denied.status_code == 409
    assert "real business action" in denied.json()["detail"]


def test_tutorial_observes_real_ceo_document_engineer_and_project_actions(client):
    founder = _new_founder(client, "domain-actions@example.com")
    departments = {item["slug"]: item["id"] for item in founder["company"]["departments"]}
    client.post("/api/v1/tutorial/start")
    client.post("/api/v1/tutorial/steps/company_setup/complete")

    ceo = client.post(
        "/api/v1/employees",
        json={
            "name": "Tutorial CEO",
            "slug": "tutorial-ceo-domain-actions",
            "role": "ceo",
            "title": "CEO",
            "department_id": departments["executive"],
            "runtime_type": "mock",
        },
    )
    assert ceo.status_code == 201
    assert client.get("/api/v1/tutorial").json()["current_step"] == "cloud_docs"

    uploaded = client.post(
        "/api/v1/drive/files?zone=knowledge&name=company-welcome.md",
        content=b"# Company Welcome",
        headers={"Content-Type": "text/markdown"},
    )
    assert uploaded.status_code == 201
    assert client.get("/api/v1/tutorial").json()["current_step"] == "git_setup"
    client.post("/api/v1/tutorial/steps/git_setup/skip")

    engineer = client.post(
        "/api/v1/employees",
        json={
            "name": "Tutorial Engineer",
            "slug": "tutorial-engineer-domain-actions",
            "role": "engineer",
            "title": "Software Engineer",
            "department_id": departments["engineering"],
            "runtime_type": "mock",
        },
    )
    assert engineer.status_code == 201
    assert client.get("/api/v1/tutorial").json()["current_step"] == "hire_qa"
    client.post("/api/v1/tutorial/steps/hire_qa/skip")

    project = client.post(
        "/api/v1/projects",
        json={"name": "Tutorial Snake Domain Actions", "description": "Tutorial project"},
    )
    assert project.status_code == 201
    assert client.get("/api/v1/tutorial").json()["current_step"] == "requirements_review"


def test_tutorial_center_lists_replayable_chapters(client):
    _new_founder(client, "center@example.com")
    response = client.get("/api/v1/tutorial/center")
    assert response.status_code == 200
    ids = {chapter["id"] for chapter in response.json()}
    assert {"ceo-setup", "provider-setup", "cloud-docs", "git", "projects", "delivery"} <= ids
