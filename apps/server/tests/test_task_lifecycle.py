"""§3.4.4: task state machine — valid and invalid transitions."""

import pytest

from app.repositories import project as project_repo

pytestmark = pytest.mark.usefixtures("client")


@pytest.fixture()
def bare_task(db, default_company_id):
    project = project_repo.create_project(
        db, company_id=default_company_id, name="Lifecycle", description="x"
    )
    task = project_repo.create_task(
        db,
        project_id=project.id,
        title="lifecycle task",
        kind="general",
        status="backlog",
        assignee_id=None,  # unassigned: the dispatcher must never pick it up
    )
    db.commit()
    yield task.id


def _patch(client, task_id, status):
    return client.patch(f"/api/v1/tasks/{task_id}", json={"status": status})


def test_valid_transitions(client, bare_task):
    assert _patch(client, bare_task, "todo").status_code == 200
    assert _patch(client, bare_task, "in_progress").status_code == 200
    assert _patch(client, bare_task, "in_review").status_code == 200
    assert _patch(client, bare_task, "rejected").status_code == 200
    # rejected→todo allowed (QA 驳回重做)
    assert _patch(client, bare_task, "todo").status_code == 200
    assert _patch(client, bare_task, "in_progress").status_code == 200
    assert _patch(client, bare_task, "in_review").status_code == 200
    assert _patch(client, bare_task, "done").status_code == 200
    assert client.get(f"/api/v1/tasks/{bare_task}").json()["status"] == "done"


def test_invalid_transitions(client, db, default_company_id):
    project = project_repo.create_project(
        db, company_id=default_company_id, name="Lifecycle2", description="x"
    )
    task = project_repo.create_task(
        db, project_id=project.id, title="t2", kind="general", status="backlog"
    )
    db.commit()

    assert _patch(client, task.id, "in_progress").status_code == 400  # backlog→in_progress
    assert _patch(client, task.id, "done").status_code == 400  # backlog→done
    assert _patch(client, task.id, "todo").status_code == 200
    assert _patch(client, task.id, "done").status_code == 400  # todo→done
    assert _patch(client, task.id, "in_progress").status_code == 200
    assert _patch(client, task.id, "todo").status_code == 400  # in_progress→todo
    assert _patch(client, task.id, "in_review").status_code == 200
    assert _patch(client, task.id, "done").status_code == 200
    assert _patch(client, task.id, "todo").status_code == 400  # done is terminal
    assert _patch(client, task.id, "nonsense").status_code == 422  # unknown status value
