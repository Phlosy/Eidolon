"""U14 file transport acceptance; domain acceptance/review is a separate gate.

Only the isolated test relay enables native tools. Workspaces, model answers,
manifest bytes and artifact responses are produced by the pinned runtimes.
"""

import asyncio
import hashlib
import json
import uuid
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from test_m3_management_bridge import manager as manager_fixture

from app.models.base import utcnow
from app.models.organization import Employee
from app.models.project import Project, Task
from app.models.provider import Provider
from app.models.runtime_execution import RuntimeAttempt
from app.repositories import persons
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeEventKind, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.runtimes.paths import HERMES_MOUNT, OPENCLAW_CONFIG_MOUNT
from app.work import telemetry

pytestmark = pytest.mark.integration
manager = manager_fixture


@pytest.fixture()
def docker_service(docker_service, monkeypatch):
    create = docker_service.create_container

    def with_native_tools(**kwargs):
        if (kwargs.get("labels") or {}).get("eidolon.test") == "model-budget-relay":
            kwargs["environment"]["ALLOW_NATIVE_TOOLS"] = "1"
        # These documented runtime settings limit only the disposable test's
        # native catalog. The relay still reserves every byte actually sent.
        for source, mount in (kwargs.get("volumes") or {}).items():
            if mount["bind"] == OPENCLAW_CONFIG_MOUNT:
                path = Path(source) / "openclaw.json"
                config = json.loads(path.read_text())
                config["tools"] = {"allow": ["read", "write", "exec"]}
                path.write_text(json.dumps(config))
            elif mount["bind"] == HERMES_MOUNT:
                path = Path(source) / "config.yaml"
                config = yaml.safe_load(path.read_text()) or {}
                config.setdefault("platform_toolsets", {})["api_server"] = ["terminal", "file"]
                path.write_text(yaml.safe_dump(config))
        return create(**kwargs)

    monkeypatch.setattr(docker_service, "create_container", with_native_tools)
    return docker_service


@pytest.fixture()
def make_employee(make_employee, idb):
    def create():
        employee = make_employee()
        with idb() as db:
            employee = db.get(Employee, employee.id)
            person = persons.create_person(db, slug=employee.slug, name=employee.name)
            employee.person_id = person.id
            db.commit()
            return employee

    return create


@pytest.mark.parametrize("runtime_type", ["hermes", "openclaw"])
@pytest.mark.parametrize("model_relay", [{"max_requests": 8, "output_limit": 1024}], indirect=True)
async def test_pinned_runtime_writes_actual_file_with_original_attempt(
    runtime_type, manager, create_model_runtime, model_relay, idb
):
    runtime = await create_model_runtime(runtime_type, manager)
    adapter = {"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type]()
    nonce = uuid.uuid4().hex[:12]
    body = f'def acceptance_marker():\n    return "{nonce}"\n'
    filename = f"result-{nonce}.py"
    prompt = (
        "Use native file/terminal tools to create one real UTF-8 source file named "
        f"{filename}. Its exact bytes, including the final newline, are this JSON string: "
        f"{json.dumps(body)}. Read the file back to verify it. "
        "Do only this local file task. No network or external messaging is needed. "
    )
    prompt += (
        "Use the Required output protocol below: write the file in its directory, "
        "then compute the actual hash and byte count and atomically publish manifest.json. "
        "The artifact type is source_code. Finish with FILE_READY."
    )
    with idb() as db:
        employee = db.get(Employee, runtime.instance.employee_id)
        provider = db.scalar(select(Provider).where(Provider.company_id == employee.company_id))
        project = Project(
            company_id=employee.company_id, name="U14 transport", status="in_progress"
        )
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            title="Produce real file",
            assignee_id=employee.id,
            status="in_progress",
            produces_json=["source_code"],
        )
        db.add(task)
        db.flush()
        context = TaskContext(
            task_id=task.id,
            project_id=project.id,
            title=task.title,
            kind="coding",
            required_artifact_types=["source_code"],
        )
        connection = resolve_container_instance(employee.id, runtime_type, session_factory=idb)
        current = await adapter.create_session(connection, context)
        work = project_repo.create_work_session(
            db,
            task_id=task.id,
            employee_id=employee.id,
            runtime_type=runtime_type,
            runtime_instance_id=runtime.instance.id,
            provider_id=provider.id,
            model=model_relay.model,
            started_at=utcnow(),
            runtime_session_ref=current.id,
        )
        db.commit()
    attempt = telemetry.prepare_attempt(
        work.id,
        current,
        prompt,
        request_key="artifact-transport",
        kind="worker",
        request_context={"required_artifact_types": ["source_code"]},
        session_factory=idb,
    )
    kinds = []
    try:
        await adapter.send_task(
            current,
            prompt,
            {"task_context": context, "runtime_config": {"run_timeout_seconds": 150}},
        )
        async with asyncio.timeout(165):
            async for event in adapter.stream_events(current):
                attempt.observe(event)
                kinds.append(event.kind.value)
                if event.kind == RuntimeEventKind.error:
                    pytest.fail(f"runtime returned {event.data}")
        assert current.status == "completed"
        outputs = await adapter.get_artifacts(current)
        from app.runtimes.workspace_artifacts import output_directory

        native_file = Path(connection.home_path) / output_directory(current) / filename
        print(
            "REAL_ARTIFACT_TRANSPORT="
            + json.dumps(
                {
                    "runtime": runtime_type,
                    "image_id": runtime.instance.image_digest,
                    "version": runtime.instance.runtime_version,
                    "session_ref": current.id,
                    "run_id": current.remote_run_id,
                    "remote_session_ref": current.remote_session_ref,
                    "attempt_id": attempt.attempt_id,
                    "event_counts": dict(Counter(kinds)),
                    "expected_body": body,
                    "expected_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "workspace_file_matches": native_file.is_file()
                    and native_file.read_bytes() == body.encode(),
                    "artifacts": [
                        {
                            "type": a.type,
                            "title": a.title,
                            "content": a.content,
                            "origin": asdict(a.origin) if a.origin else None,
                        }
                        for a in outputs
                    ],
                    "gate": "transport only; no handoff or review claimed",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        assert len(outputs) == 1, "completed runtime did not expose the actual file artifact"
        artifact = outputs[0]
        assert artifact.content == body
        assert artifact.origin.local_session_ref == current.id
        assert artifact.origin.remote_run_id == current.remote_run_id
        assert artifact.origin.sha256 == hashlib.sha256(body.encode()).hexdigest()
        assert artifact.origin.size_bytes == len(body.encode())
        with idb() as db:
            saved = db.get(RuntimeAttempt, attempt.attempt_id)
            assert saved.status == "completed" and saved.remote_run_id == current.remote_run_id
            assert saved.local_session_ref == current.id
        receipt = json.loads(model_relay.receipt.read_text())
        assert receipt["allow_native_tools"] is True
        assert 2 <= receipt["attempts"] <= 8
        assert all(item["state"] == "settled" for item in receipt["requests"])
        assert Decimal(receipt["reserved_cost_usd"]) == 0
        assert Decimal(receipt["cost_usd"]) <= Decimal("0.05")
    finally:
        if current.background is not None and not current.background.done():
            await adapter.cancel_task(current)
        if current.background is not None:
            await asyncio.gather(current.background, return_exceptions=True)
