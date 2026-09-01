"""§4.3: MockAdapter event stream ends with completed and produces an artifact."""

import asyncio

from app.runtimes.base import EmployeeRef, RuntimeEventKind, TaskContext
from app.runtimes.mock.adapter import MockAdapter

CTX = TaskContext(
    task_id=1,
    project_id=1,
    title="调研 todo CLI 技术方案",
    kind="research",
    description="Build a todo CLI",
    acceptance_criteria="调研报告含 sources",
    employee_name="Bob",
    employee_role="researcher",
)


def _run_stream(config: dict):
    async def main():
        adapter = MockAdapter()
        instance = await adapter.create_instance(
            EmployeeRef(id=1, slug="bob", name="Bob", role="researcher"), config
        )
        await adapter.start(instance)
        session = await adapter.create_session(instance, CTX)
        await adapter.send_task(session, "prompt", {"task_context": CTX, "runtime_config": config})
        kinds = []
        async for event in adapter.stream_events(session):
            kinds.append(event.kind)
        artifacts = await adapter.get_artifacts(session)
        await adapter.stop(instance)
        return kinds, artifacts

    return asyncio.run(main())


def test_mock_runtime_completes_with_artifact():
    kinds, artifacts = _run_stream({})
    assert kinds[0] == RuntimeEventKind.thinking
    assert RuntimeEventKind.message in kinds
    assert RuntimeEventKind.status in kinds
    assert RuntimeEventKind.artifact in kinds
    assert kinds[-1] == RuntimeEventKind.completed
    assert len(artifacts) == 1
    assert artifacts[0].type == "research_report"
    assert "## Sources" in artifacts[0].content


def test_mock_runtime_failure_injection():
    kinds, artifacts = _run_stream({"mock_fail_rate": 1.0})
    assert kinds[-1] == RuntimeEventKind.error
    assert artifacts == []
