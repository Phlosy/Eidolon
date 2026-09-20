"""U14 real file -> formal review -> exact downstream input -> formal review.

Four isolated, independently capped stages. The host supplies task requirements,
never artifact files, model replies, or verdicts. Default execution is disabled.
"""

import ast
import asyncio
import hashlib
import json
import os
import secrets
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select
from test_m3_management_bridge import FIXED_IMAGES

from app.core.config import settings
from app.models.base import utcnow
from app.models.decision import DecisionRecord, ToolAudit
from app.models.organization import Company, Department, Employee
from app.models.position import PositionAssignment, PositionDefinition, PositionSlot
from app.models.project import Project, Task, WorkSession
from app.models.provider import Provider
from app.models.review import ReviewRequest
from app.models.runtime_artifact import RuntimeArtifactCollection
from app.providers.secrets.store import get_secret_store
from app.repositories import persons
from app.repositories import project as project_repo
from app.runtimes.base import RuntimeEventKind, TaskContext
from app.runtimes.container_common import resolve_container_instance
from app.runtimes.hermes.adapter import HermesAdapter
from app.runtimes.manager.docker_manager import DockerRuntimeInstanceManager
from app.runtimes.openclaw.adapter import OpenClawAdapter
from app.runtimes.paths import HERMES_MOUNT, OPENCLAW_CONFIG_MOUNT
from app.services import drive
from app.work import authority, contracts, handoff, reviews, telemetry
from app.work.runtime_conversation import ConversationLimits, run_conversation
from app.workflow.orchestrator import Orchestrator

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("EIDOLON_REAL_HANDOFF_TESTS") != "1",
        reason="real handoff needs its separate finite allowance",
    ),
]
STAGES = ("upstream", "upstream-review", "downstream", "downstream-review")


@pytest.fixture()
def docker_service(docker_service, monkeypatch):
    create = docker_service.create_container

    def configure_stage(**kwargs):
        labels = kwargs.get("labels") or {}
        if labels.get("eidolon.test") == "model-budget-relay":
            # Reviewers use the existing controlled-turn text relay, as in U7/U8.
            # Only workers need native tools to create their actual files.
            kwargs["environment"]["ALLOW_NATIVE_TOOLS"] = (
                "0" if labels["eidolon.stage"].endswith("review") else "1"
            )
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

    monkeypatch.setattr(docker_service, "create_container", configure_stage)
    return docker_service


@pytest.fixture()
def chain_runtime(
    idb, docker_service, cleanup_containers, runtime_network, wait_control, tmp_path, monkeypatch
):
    """Owned resources only; per-stage max8 requests, 1024 output tokens, USD.05.

    The shared fixture's one-relay allowance is intentionally not enlarged. Each
    stage gets a separate immutable ledger; the launcher caps complete attempts.
    """
    assert os.environ.get("EIDOLON_REAL_MODEL_TESTS") == "1"
    key = os.environ["EIDOLON_TEST_PROVIDER_KEY"]
    budget = Decimal(os.environ["EIDOLON_TEST_MODEL_BUDGET_USD"])
    assert budget.is_finite() and 0 < budget <= Decimal("0.05")
    expiry = datetime.fromisoformat(os.environ["EIDOLON_TEST_PRICING_VALID_UNTIL"])
    assert expiry.tzinfo and datetime.now(UTC) < expiry <= datetime.now(UTC) + timedelta(hours=2)
    relay_image = docker_service.inspect_image("python:3.12-slim")
    assert relay_image is not None
    for kind, image in FIXED_IMAGES.items():
        assert docker_service.inspect_image(image) is not None, f"missing fixed {kind} image"
        monkeypatch.setattr(settings, f"{kind}_image", image)
    monkeypatch.setattr("app.workflow.orchestrator.SessionLocal", idb)
    monkeypatch.setattr(settings, "allow_planning_fixtures", False)
    monkeypatch.setattr(settings, "orchestrator_dispatch_enabled", False)
    manager = DockerRuntimeInstanceManager(docker=docker_service)
    with idb() as db:
        company = Company(name="U14 actual handoff", slug=f"u14-{uuid.uuid4().hex[:12]}")
        db.add(company)
        db.commit()
    stages = {}

    async def create(stage, runtime_type):
        assert stage in STAGES and stage not in stages and len(stages) < 4
        evidence = tmp_path / f"budget-{stage}"
        evidence.mkdir(mode=0o700)
        name, token = f"eidolon-it-budget-{uuid.uuid4().hex[:12]}", secrets.token_hex(24)
        assert docker_service.ensure_network(runtime_network)
        cid = docker_service.create_container(
            name=name,
            image=relay_image["Id"],
            network=runtime_network,
            environment={
                "REAL_PROVIDER_KEY": key,
                "RELAY_TOKEN": token,
                "MAX_COST_USD": str(budget),
                "MAX_REQUESTS": "8",
                "OUTPUT_LIMIT": "1024",
                "PRICING_VALID_UNTIL": expiry.isoformat(),
            },
            volumes={
                str(Path(__file__).with_name("model_budget_proxy.py")): {
                    "bind": "/relay.py",
                    "mode": "ro",
                },
                str(evidence): {"bind": "/evidence", "mode": "rw"},
            },
            restart_policy="no",
            cpu_limit=0.5,
            memory_limit_mb=128,
            user=f"{os.getuid()}:{os.getgid()}",
            command=["python", "/relay.py"],
            labels={"eidolon.test": "model-budget-relay", "eidolon.stage": stage},
        )
        # Register the ledger before later setup can fail; cleanup_containers
        # tracks the actual ID before any start/provisioning failure too.
        relay = SimpleNamespace(
            url=f"http://{name}:8642/v1",
            token=token,
            model="deepseek-flash",
            receipt=evidence / "receipt.json",
        )
        stages[stage] = relay
        assert cid and docker_service.start_container(cid)
        deadline = time.monotonic() + 10
        while not relay.receipt.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        assert relay.receipt.exists()
        with idb() as db:
            person = persons.create_person(db, slug=f"u14-{stage}", name=stage)
            department = Department(company_id=company.id, slug=stage, name=stage)
            position = PositionDefinition(
                company_id=company.id,
                code=stage,
                name=stage,
                job_family="management" if stage.endswith("review") else "engineering",
                responsibilities=[
                    "Perform the bound task using actual inputs and report exact results."
                ],
            )
            db.add_all([department, position])
            db.flush()
            employee = Employee(
                company_id=company.id,
                person_id=person.id,
                department_id=department.id,
                slug=f"u14-{stage}",
                name=stage,
                status="idle",
                runtime_type=runtime_type,
                workspace_path=f"{settings.workspace_root}/u14-{stage}",
                memory_namespace=f"u14-{stage}",
            )
            slot = PositionSlot(
                company_id=company.id,
                department_id=department.id,
                position_definition_id=position.id,
                administrative_status="active",
                slot_code=stage,
            )
            db.add_all([employee, slot])
            db.flush()
            db.add(
                PositionAssignment(
                    employee_id=employee.id,
                    department_id=department.id,
                    position_slot_id=slot.id,
                    employment_status="active",
                    assignment_type="primary",
                    is_primary=True,
                    position_title_snapshot=stage,
                )
            )
            if stage.endswith("review"):
                authority.grant_authority(
                    db,
                    position_definition_id=position.id,
                    kind=contracts.AuthorityKind.accept_delivery,
                    commit=False,
                )
            provider = Provider(
                company_id=company.id,
                name=stage,
                provider_type="custom",
                base_url=relay.url,
                scope="company",
                enabled=True,
                credential_ref=get_secret_store().store(db, relay.token),
                metadata_json={},
            )
            db.add(provider)
            db.commit()
            instance = await manager.create_instance(
                db,
                employee,
                runtime_type=runtime_type,
                deployment_mode="docker",
                provider=provider,
                model=relay.model,
            )
            control = manager._secrets.retrieve(db, instance.metadata_json["api_key_ref"])
            device = manager._secrets.retrieve(db, instance.metadata_json.get("device_key_ref"))
        facts = await wait_control(manager, instance, control, device)
        return SimpleNamespace(
            stage=stage,
            relay=relay,
            employee=employee,
            provider=provider,
            instance=instance,
            manager=manager,
            control_facts=facts,
            adapter={"hermes": HermesAdapter, "openclaw": OpenClawAdapter}[runtime_type](),
            connection=resolve_container_instance(employee.id, runtime_type, session_factory=idb),
        )

    yield create
    # Failure receipts are just as important as success receipts; never erase or
    # reset a stage ledger. Owned container/network cleanup belongs to fixtures.
    for stage, relay in stages.items():
        if relay.receipt.exists():
            print(
                "REAL_HANDOFF_BUDGET="
                + json.dumps(
                    {
                        "stage": stage,
                        "receipt": json.loads(relay.receipt.read_text()),
                    },
                    sort_keys=True,
                )
            )


def assert_stage_budget(runtime):
    receipt = json.loads(runtime.relay.receipt.read_text())
    assert receipt["allow_native_tools"] is (not runtime.stage.endswith("review"))
    assert 1 <= receipt["attempts"] <= 8
    assert all(r["state"] == "settled" for r in receipt["requests"])
    assert Decimal(receipt["reserved_cost_usd"]) == 0
    assert Decimal(receipt["cost_usd"]) <= Decimal("0.05")


async def produce(idb, runtime, task_id):
    with idb() as db:
        task = db.get(Task, task_id)
        refs = handoff.resolve_input_artifacts(db, task)
        context = TaskContext(
            task_id=task.id,
            project_id=task.project_id,
            title=task.title,
            kind=task.kind,
            description=task.description,
            acceptance_criteria=task.acceptance_criteria,
            input_artifacts=[r.as_dict() for r in refs],
            required_artifact_types=["source_code"],
        )
        current = await runtime.adapter.create_session(runtime.connection, context)
        work = project_repo.create_work_session(
            db,
            task_id=task.id,
            employee_id=runtime.employee.id,
            runtime_type=runtime.instance.runtime_type,
            runtime_instance_id=runtime.instance.id,
            provider_id=runtime.provider.id,
            model=runtime.relay.model,
            started_at=utcnow(),
            runtime_session_ref=current.id,
        )
        handoff.record_consumed(
            db,
            task_id=task.id,
            artifact_ids=[r.artifact_id for r in refs],
            artifact_refs=refs,
            work_session_id=work.id,
            actor_employee_id=runtime.employee.id,
            commit=False,
        )
        db.commit()
    prompt = (
        context.description
        + "\nAcceptance criteria: "
        + context.acceptance_criteria
        + "\nUse native file/terminal tools. Produce exactly one UTF-8 Python source file in the "
        "Required output protocol directory. Read it back, compute its actual SHA-256 and byte "
        "count, then atomically publish manifest.json with type source_code. "
        "Do only this local task; no network or external messaging. Finish with FILE_READY."
    )
    if refs:
        prompt += "\nActual accepted inputs:\n" + json.dumps(
            context.input_artifacts, ensure_ascii=False
        )
    attempt = telemetry.prepare_attempt(
        work.id,
        current,
        prompt,
        request_key="worker",
        kind="worker",
        request_context={"input_artifacts": context.input_artifacts},
        session_factory=idb,
    )
    try:
        await runtime.adapter.send_task(
            current,
            prompt,
            {
                "task_context": context,
                "runtime_config": {"run_timeout_seconds": 150},
            },
        )
        async with asyncio.timeout(165):
            async for event in runtime.adapter.stream_events(current):
                attempt.observe(event)
                assert event.kind != RuntimeEventKind.error, "actual worker runtime failed"
        assert current.status == "completed"
        outputs = await runtime.adapter.get_artifacts(current)
        assert len(outputs) == 1 and outputs[0].origin is not None
        from app.runtimes.workspace_artifacts import output_directory

        directory = Path(runtime.connection.home_path) / output_directory(current)
        manifest = json.loads((directory / "manifest.json").read_text())
        entry = next(a for a in manifest["artifacts"] if a["sha256"] == outputs[0].origin.sha256)
        original_file = directory / entry["path"]
        assert original_file.read_bytes() == outputs[0].content.encode("utf-8")
        Orchestrator()._finalize(
            task_id, True, outputs, None, 1.0, work_session_id=work.id, runtime_session=current
        )
        attempt.finish_host("completed", "stream_completed")
        with idb() as db:
            assert db.get(Task, task_id).status == "in_review"
            selected = handoff.produced_artifacts(db, task_id)
            assert len(selected) == len(outputs) == 1
            from app.models.drive import DriveNode, DriveRevision

            node = db.get(DriveNode, selected[0].artifact_id)
            revision = db.get(DriveRevision, selected[0].revision_id)
            content = drive.read_revision_content(node, revision)
            assert content == outputs[0].content
            assert hashlib.sha256(content.encode()).hexdigest() == selected[0].sha256
            collection = db.get(RuntimeArtifactCollection, attempt.attempt_id)
            assert collection.entries_json[0]["revision_id"] == revision.id
            consumed = [r.as_dict() for r in handoff.consumed_artifacts(db, task_id)]
        assert_stage_budget(runtime)
        evidence = {
            "stage": runtime.stage,
            "runtime": runtime.instance.runtime_type,
            "image_id": runtime.instance.image_digest,
            "version": runtime.instance.runtime_version,
            "work_session_id": work.id,
            "local_session_ref": current.id,
            "run_id": current.remote_run_id,
            "attempt_id": attempt.attempt_id,
            "origin": asdict(outputs[0].origin),
            "workspace_file": str(original_file),
            "artifact": selected[0].as_dict(),
            "body": content,
            "input_refs": context.input_artifacts,
            "consumed": consumed,
        }
        print("REAL_HANDOFF_WORKER=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        return evidence
    finally:
        if current.background is not None and not current.background.done():
            await runtime.adapter.cancel_task(current)
        if current.background is not None:
            await asyncio.gather(current.background, return_exceptions=True)


async def review(idb, runtime, task_id, requester_id, monkeypatch):
    with idb() as db:
        task = db.get(Task, task_id)
        request = reviews.open_review_request(
            db,
            task=task,
            requester_employee_id=requester_id,
            reviewer_employee_id=runtime.employee.id,
            reason="Read the actual saved artifact body and task criteria, then give your verdict.",
        )
        context = TaskContext(
            task_id=None, project_id=task.project_id, title="Formal artifact review", kind="review"
        )
        current = await runtime.adapter.create_session(runtime.connection, context)
        work = project_repo.create_work_session(
            db,
            purpose="review",
            task_id=None,
            project_id=task.project_id,
            review_request_id=request.id,
            employee_id=runtime.employee.id,
            runtime_type=runtime.instance.runtime_type,
            runtime_instance_id=runtime.instance.id,
            provider_id=runtime.provider.id,
            model=runtime.relay.model,
            started_at=utcnow(),
            runtime_session_ref=current.id,
        )
        db.commit()
    transcript = []
    complete = runtime.adapter.complete_turn

    async def capture(session, prompt, **kwargs):
        reply = await complete(session, prompt, **kwargs)
        transcript.append(
            {
                "run_id": reply.run_id,
                "response": reply.text,
                "feedback": json.loads(prompt).get("feedback"),
            }
        )
        return reply

    async def budget_check(work_session_id, request_id):
        assert work_session_id == work.id
        receipt = json.loads(runtime.relay.receipt.read_text())
        return receipt["attempts"] < 8

    monkeypatch.setattr(runtime.adapter, "complete_turn", capture)
    result = await run_conversation(
        work.id,
        adapter=runtime.adapter,
        runtime_session=current,
        goal=(
            f"Use only the host JSON reply protocol, never native tools. First inspect_task_review "
            f"for task_id {task_id}. Read the actual source_code body with inspect_artifact using "
            "the artifact id in those facts; if the task has upstream inputs, inspect their "
            "bodies too. Evaluate the exact task acceptance criteria. Then submit your actual "
            "verdict using one accept_delivery decision with one submit_review_verdict action "
            f"for review_request_id {request.id}. Use decision.scope string task:{task_id}; "
            "nest decision_type, reason, scope and actions inside decision. Return each request "
            "as your final text response so the host can execute it. Explain the observed "
            "content concisely in notes. Do not assume it passes or invent execution receipts. "
            "Only after the host returns the saved verdict result, finish with summary exactly "
            "review=<review request id>;audit=<submit_review_verdict audit id>. "
            "Use distinct operation_key values for new requests. Keep JSON concise."
        ),
        budget_check=budget_check,
        limits=ConversationLimits(
            max_rounds=6, max_actions=8, turn_timeout_seconds=60, total_timeout_seconds=210
        ),
        session_factory=idb,
    )
    with idb() as db:
        request = db.get(ReviewRequest, request.id)
        audits = list(
            db.scalars(
                select(ToolAudit).where(ToolAudit.work_session_id == work.id).order_by(ToolAudit.id)
            )
        )
        writes = [a for a in audits if a.tool_name == "submit_review_verdict"]
        evidence = {
            "stage": runtime.stage,
            "runtime": runtime.instance.runtime_type,
            "image_id": runtime.instance.image_digest,
            "version": runtime.instance.runtime_version,
            "work_session_id": work.id,
            "review_request_id": request.id,
            "verdict": request.verdict,
            "task_status": db.get(Task, task_id).status,
            "result": result,
            "transcript": transcript,
            "audits": [
                {
                    "id": a.id,
                    "tool": a.tool_name,
                    "outcome": a.outcome,
                    "arguments": a.arguments_json,
                    "result": a.result_json,
                    "actor_person_id": a.actor_person_id,
                    "decision_id": a.decision_id,
                }
                for a in audits
            ],
        }
        print("REAL_HANDOFF_REVIEW=" + json.dumps(evidence, ensure_ascii=False, sort_keys=True))
        assert result["status"] == "finished", result
        assert request.verdict == "PASS" and request.status == "decided"
        assert db.get(Task, task_id).status == "done"
        assert len(writes) == 1 and writes[0].outcome == "applied"
        decision = db.get(DecisionRecord, writes[0].decision_id)
        assert (
            decision.status == "APPLIED" and decision.actor_person_id == runtime.employee.person_id
        )
        output_ids = {r.artifact_id for r in handoff.produced_artifacts(db, task_id)}
        input_ids = {r.artifact_id for r in handoff.consumed_artifacts(db, task_id)}
        reads = {
            a.arguments_json.get("artifact_id")
            for a in audits
            if a.tool_name == "inspect_artifact" and a.outcome == "read" and a.id < writes[0].id
        }
        assert output_ids | input_ids <= reads
        assert any(
            a.tool_name == "inspect_task_review" and a.outcome == "read" and a.id < writes[0].id
            for a in audits
        )
        assert result["summary"] == f"review={request.id};audit={writes[0].id}"
        host_work = db.get(WorkSession, work.id)
        host_work.status, host_work.ended_at = "completed", utcnow()
        host_work.summary = result["summary"]
        db.commit()
    assert_stage_budget(runtime)
    return evidence


def literal_return(content, name):
    function = next(
        n for n in ast.parse(content).body if isinstance(n, ast.FunctionDef) and n.name == name
    )
    returns = [n for n in ast.walk(function) if isinstance(n, ast.Return)]
    assert len(returns) == 1
    return ast.literal_eval(returns[0].value)


async def test_actual_upstream_to_downstream_and_formal_reviews(chain_runtime, idb, monkeypatch):
    upstream = await chain_runtime("upstream", "hermes")
    nonce = uuid.uuid4().hex[:12]
    with idb() as db:
        project = Project(
            company_id=upstream.employee.company_id,
            name="U14 real handoff",
            status="in_progress",
            work_mode="managed",
            planning_fixture="none",
        )
        db.add(project)
        db.flush()
        task = Task(
            project_id=project.id,
            title="Produce original source",
            kind="development",
            assignee_id=upstream.employee.id,
            status="in_progress",
            produces_json=["source_code"],
            description=(
                "Create source.py with a function acceptance_marker "
                f"whose literal return is {nonce!r}."
            ),
            acceptance_criteria=(
                f"The Python source defines acceptance_marker() returning literal {nonce!r}."
            ),
        )
        db.add(task)
        db.commit()
    original = await produce(idb, upstream, task.id)
    assert literal_return(original["body"], "acceptance_marker") == nonce
    first_reviewer = await chain_runtime("upstream-review", "hermes")
    await review(idb, first_reviewer, task.id, upstream.employee.id, monkeypatch)
    downstream = await chain_runtime("downstream", "openclaw")
    with idb() as db:
        second = Task(
            project_id=project.id,
            title="Transform accepted input",
            kind="development",
            assignee_id=downstream.employee.id,
            status="in_progress",
            produces_json=["source_code"],
            description=(
                "Read the actual accepted Python input. Create derived.py with function "
                "derived_marker. Its literal return must be the input acceptance_marker "
                "return value followed by ':consumed'."
            ),
            acceptance_criteria=(
                "Valid Python defining derived_marker() whose literal return equals the "
                "original input acceptance_marker literal plus ':consumed'."
            ),
        )
        db.add(second)
        db.flush()
        handoff.consume_artifact(db, task=second, artifact_id=original["artifact"]["artifact_id"])
    derived = await produce(idb, downstream, second.id)
    assert literal_return(derived["body"], "derived_marker") == nonce + ":consumed"
    assert derived["input_refs"][0]["excerpt"] == original["body"]
    assert derived["input_refs"][0]["sha256"] == original["artifact"]["sha256"]
    assert derived["consumed"][0]["revision_id"] == original["artifact"]["revision_id"]
    last_reviewer = await chain_runtime("downstream-review", "openclaw")
    final = await review(idb, last_reviewer, second.id, downstream.employee.id, monkeypatch)
    assert any(
        a["tool"] == "inspect_artifact"
        and a["arguments"].get("artifact_id") == original["artifact"]["artifact_id"]
        for a in final["audits"]
    )
