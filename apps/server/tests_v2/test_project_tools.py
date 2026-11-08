"""Eyes and hands for managers (R7-c): a decision run can look at the project —
status, artifacts, workspace files, tests — read-only and gated by the role's grants;
its money is capped by the institution's run_default like any other run."""

from uuid import uuid4

from project_support import set_up_project, store, svc
from test_agent_loop import ScriptedLoop
from test_money import OWNER, found
from test_work import run_through

from eidolon.agent import run as R
from eidolon.agent.blobs import BlobStore
from eidolon.agent.managers import Assigner, Deciders, Person, Roster, StaticRoster
from eidolon.agent.project_tools import DESCRIPTORS, NAMES, TOOLS_HINT, ProjectTools
from eidolon.agent.providers import TextDelta
from eidolon.agent.providers.base import ToolCall
from eidolon.agent.providers.base import ToolCallEvent as TCE
from eidolon.agent.scheduler import Workspaces
from eidolon.db import SessionLocal
from eidolon.institution import customize
from eidolon.institution.schema import MICRO_PER_UNIT
from eidolon.institution.templates import SOFTWARE_STUDIO_V1 as T
from eidolon.kernel.consumers import process
from eidolon.work import WorkProjection


def call(n, name, **args):
    return [TextDelta("looking"), TCE(ToolCall(f"c{n}", name, args))]


def staffed_project(db, tmp_path):
    """A real company with a finished task whose workspace holds a file and a test."""
    company = found(db)
    company, pid, task = set_up_project(db, company)
    run_through(db, company, pid, task, "eng-1", artifact=True)  # artifact out.txt (blob aaaa…)
    ws = Workspaces(tmp_path / "ws")
    root = ws.for_task(company, pid, task)
    (root / "hello.py").write_text("print('Hello, Eidolon!')\n")
    (root / "test_hello.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n")
    (root / "notes").mkdir()
    (root / "notes" / "todo.md").write_text("- nothing\n")
    db.commit()
    process(SessionLocal, WorkProjection())
    return company, pid, task, ws


def test_tools_are_read_only_and_confined_to_the_project(db, tmp_path):
    company, pid, task, ws = staffed_project(db, tmp_path)
    blobs = BlobStore(tmp_path / "blobs")
    tools = ProjectTools(SessionLocal, svc, blobs, ws, pid)
    assert set(NAMES) == {n.spec.name for n in DESCRIPTORS}
    assert not {d.name for d in DESCRIPTORS} & {"write_file", "shell"}
    assert all(d.side_effect != "HIGH_IMPACT" for d in DESCRIPTORS)
    assert {d.side_effect for d in DESCRIPTORS if d.name != "run_tests"} == {"READ"}
    status = tools.project_status()
    assert status["project"] == "Greeting" and status["tasks"][0]["status"] == "done"
    assert status["tasks"][0]["reviews"][0]["reviewer"] == "qa-1"
    arts = tools.list_artifacts()
    assert arts[0]["path"] == "out.txt" and arts[0]["task"] == "Greeting CLI"
    assert tools.read_artifact("out.txt").startswith("[content of out.txt is no longer stored]")
    listing = tools.list_dir("Greeting CLI")
    assert "hello.py" in listing and "notes/" in listing and "notes/todo.md" in listing
    assert tools.list_dir("t1", "notes", depth=1) == "notes/todo.md (10 B)"
    assert "task_id" not in str(status) and "artifact_id" not in str(
        arts
    )  # ids never reach the model
    assert (
        tools.read_file("Greeting CLI", "hello.py")
        == "[hello.py: lines 1-1 of 1]\nprint('Hello, Eidolon!')"
    )
    for bad in ("../../etc/passwd", "/etc/passwd"):
        try:
            tools.read_file(str(task), bad)
        except PermissionError as exc:
            assert "outside" in str(exc)
        else:
            raise AssertionError("escaped the workspace")
    try:
        tools.read_file("no such task", "hello.py")
    except LookupError as exc:
        assert "t1 = Greeting CLI" in str(exc)
    else:
        raise AssertionError("unknown task accepted")


def test_managers_look_before_deciding_and_the_gate_follows_the_role(db, tmp_path):
    company, pid, task, ws = staffed_project(db, tmp_path)
    blobs = BlobStore(tmp_path / "blobs")
    # a second task to assign; the Tech Lead reads the finished one's file first
    t2 = uuid4()
    svc.create_task(
        db,
        pid,
        __import__("eidolon.kernel.facts", fromlist=["Actor"]).Actor(
            __import__("eidolon.kernel.facts", fromlist=["ActorKind"]).ActorKind.agent, "pm-1"
        ),
        task_id=t2,
        title="Second",
        description="d",
        role="engineer",
    )
    db.commit()
    process(SessionLocal, WorkProjection())
    provider = ScriptedLoop(
        [
            call(1, "project_status"),
            call(2, "read_file", task="Greeting CLI", path="hello.py"),
            call(3, "run_tests", task="Greeting CLI"),
            [TextDelta('{"assignee": "eng-2", "reason": "eng-1 is busy; hello.py looks fine"}')],
        ]
    )
    deciders = Deciders(SessionLocal, provider, blobs, workspaces=ws, svc=svc)
    roster = StaticRoster(
        Roster(
            [Person("tl-1", "tech_lead"), Person("eng-1", "engineer"), Person("eng-2", "engineer")]
        )
    )
    assigner = Assigner(SessionLocal, deciders, roster, svc=svc)
    assert assigner.tick() == 1
    assert provider.calls == 4
    assert TOOLS_HINT in provider.last_request.system
    assert {t.name for t in provider.last_request.tools} == set(NAMES)
    state = svc.project(db, pid)
    assert state.task(t2).assignee == "eng-2"
    # the run recorded every look as a fact; the tech lead may read and run tests
    run_id = next(
        f.correlation_id
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=100)
        if f.payload["person_id"] == "tl-1"
    )
    facts = store.by_correlation(db, run_id)
    requested = [f for f in facts if f.kind == R.TOOL_REQUESTED]
    completed = [f for f in facts if f.kind == R.TOOL_COMPLETED]
    assert [f.payload["name"] for f in requested] == ["project_status", "read_file", "run_tests"]
    assert all(not f.payload.get("is_error") for f in completed), [f.payload for f in completed]
    assert (
        "1 passed" in blobs.get_json(completed[-1].payload["result_ref"])["tool_result"]["content"]
    )
    state_fact = next(f for f in facts if f.kind == R.RUN_STARTED)
    assert (
        state_fact.payload["budget"]["max_cost_micro"]
        == T.budget_policy.run_default * MICRO_PER_UNIT
    )
    assert state_fact.payload["budget"]["max_steps"] == 8

    # the PM may read but not run: the institution's gate refuses, the model sees why
    provider = ScriptedLoop(
        [
            call(1, "run_tests", task="Greeting CLI"),
            call(2, "list_artifacts"),
            [TextDelta('{"recommendation": "approve", "summary": "fine", "rationale": "r"}')],
        ]
    )
    deciders = Deciders(SessionLocal, provider, blobs, workspaces=ws, svc=svc)
    pm = Person("pm-1", "pm")
    answer = deciders.ask(
        company_id=company,
        person=pm,
        system="You advise.",
        prompt="Project: Greeting",
        run_key="pm-look",
        project_id=pid,
        template=T,
    )
    assert answer and answer["recommendation"] == "approve"
    run_id = next(
        f.correlation_id
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=100)
        if f.payload["person_id"] == "pm-1"
    )
    completed = [f for f in store.by_correlation(db, run_id) if f.kind == R.TOOL_COMPLETED]
    first = blobs.get_json(completed[0].payload["result_ref"])["tool_result"]
    assert completed[0].payload["is_error"] and "not authorized" in first["content"]
    assert "workspace.execute" in first["content"]
    assert not completed[1].payload["is_error"]

    # without a workspace root there are no eyes: the old two-step JSON run, unchanged
    provider = ScriptedLoop([[TextDelta('{"verdict": "approve", "reason": "ok"}')]])
    blind = Deciders(SessionLocal, provider, blobs)
    assert blind.ask(
        company_id=company,
        person=pm,
        system="S",
        prompt="P",
        run_key="blind",
        project_id=pid,
        template=T,
    ) == {"verdict": "approve", "reason": "ok"}
    assert provider.last_request.tools == () and TOOLS_HINT not in provider.last_request.system


def test_a_company_may_take_the_eyes_away(db, tmp_path):
    """Grants are institution, not code: a company that strips workspace.read from the
    Tech Lead gets a Tech Lead who decides from the prompt alone."""
    company, pid, task, ws = staffed_project(db, tmp_path)
    store.append(
        db,
        customize(
            db,
            company,
            OWNER,
            {"authority_defaults": {"tech_lead": ["plan.approve", "task.assign", "task.review"]}},
            reason="no peeking",
        ),
    )
    db.commit()
    from eidolon.institution import current

    template = current(db, company).template
    provider = ScriptedLoop(
        [call(1, "read_file", task="Greeting CLI", path="hello.py"), [TextDelta('{"ok": true}')]]
    )
    deciders = Deciders(SessionLocal, provider, BlobStore(tmp_path / "b"), workspaces=ws, svc=svc)
    assert deciders.ask(
        company_id=company,
        person=Person("tl-1", "tech_lead"),
        system="S",
        prompt="P",
        run_key="stripped",
        project_id=pid,
        template=template,
    ) == {"ok": True}
    run_id = next(
        f.correlation_id
        for f in store.stream(db, kinds=[R.RUN_STARTED], company_id=company, limit=100)
        if f.payload["person_id"] == "tl-1"
    )
    completed = [f for f in store.by_correlation(db, run_id) if f.kind == R.TOOL_COMPLETED]
    assert completed[0].payload["is_error"]
