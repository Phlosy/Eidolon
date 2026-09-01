"""Runtime capabilities honesty + /runtime-types discovery (v0.2)."""

from app.models.enums import RuntimeType
from app.runtimes.gateway import gateway


def test_mock_adapter_capabilities_all_true():
    caps = gateway.adapters()[RuntimeType.mock].get_capabilities()
    assert all(vars(caps).values()), "mock adapter advertises every capability"


def test_hermes_capabilities_honest():
    caps = gateway.adapters()[RuntimeType.hermes].get_capabilities()
    assert caps.chat and caps.task and caps.streaming
    assert caps.scheduler is True  # /api/jobs
    assert caps.artifacts is False  # no artifacts-download endpoint


def test_openclaw_capabilities_honest():
    caps = gateway.adapters()[RuntimeType.openclaw].get_capabilities()
    assert caps.chat and caps.task and caps.streaming
    assert caps.artifacts is True  # artifacts.list/get/download RPC


def test_unimplemented_adapters_report_no_capabilities(client):
    # codex/claude_code/opencode/custom have no adapter yet: /runtime-types
    # must report implemented=false with all capabilities false.
    types = {t["type"]: t for t in client.get("/api/v1/runtime-types").json()}
    for name in ("codex", "claude_code", "opencode", "custom"):
        info = types[name]
        assert info["implemented"] is False
        assert info["deployment_modes"] == []
        assert not any(info["capabilities"].values())


def test_runtime_types_endpoint(client):
    response = client.get("/api/v1/runtime-types")
    assert response.status_code == 200
    types = {t["type"]: t for t in response.json()}
    assert set(types) == {rt.value for rt in RuntimeType}
    assert types["mock"]["implemented"] is True
    assert types["mock"]["deployment_modes"] == ["mock"]
    assert types["hermes"]["implemented"] is True
    assert types["hermes"]["deployment_modes"] == ["docker"]
    assert "docker_available" in types["hermes"]
    assert "openai" in types["hermes"]["supported_providers"]
    assert types["hermes"]["capabilities"]["artifacts"] is False
    assert types["openclaw"]["capabilities"]["artifacts"] is True
