"""P4d 后端侧契约：`EntitlementSourceOut.layer` 只能是人级/职位级两值。

前端对等测试：`apps/web/src/types/access-source-layer.contract.test.ts`
（那里的 `AccessSourceLayer` 必须与本文件读到的 OpenAPI 枚举逐字一致）。
锁三层：

1. OpenAPI 里 `layer` 的枚举恰为 {"person", "position"}、默认 "person"；
2. `app/lifecycle/access.layer_of_source()` 是唯一口径：任何来源只会落到这两值；
3. 真实 API 响应里 `sources[].layer` 不会出现第三值。
"""

from __future__ import annotations

from app.lifecycle import access
from app.models.enums import PackageSource


def _entitlement_source_schemas(client) -> list[dict]:
    """在 OpenAPI 里找到所有带 package_id + package_name + layer 的组件。"""
    spec = client.get("/openapi.json").json()
    found = []
    for name, schema in spec["components"]["schemas"].items():
        props = schema.get("properties", {})
        if {"package_id", "package_name", "layer"} <= set(props):
            found.append((name, schema))
    assert found, "OpenAPI 里找不到 EntitlementSourceOut —— 契约测试已失效"
    return found


def test_openapi_layer_enum_matches_the_frontend_union(client):
    for _name, schema in _entitlement_source_schemas(client):
        layer = schema["properties"]["layer"]
        assert layer.get("enum") == ["person", "position"], layer
        # 后端默认人级：职位层是唯一需要显式声明的例外（旧调用方不传也不会被误标）
        assert layer.get("default") == "person", layer


def test_layer_of_source_is_the_single_mapping_and_is_binary(client):
    """任何来源都只会映射到 person / position —— 没有第三层。"""
    assert access.layer_of_source(PackageSource.position.value) == "position"
    for source in (
        PackageSource.manual.value,
        PackageSource.role.value,
        PackageSource.project.value,
        None,
    ):
        assert access.layer_of_source(source) == "person", source


def test_real_access_response_never_carries_a_third_layer(client, default_company_id):
    """员工 /access 响应里的 sources[].layer 全量收集，必须是两值子集。"""
    employee_id = client.get("/api/v1/employees").json()[0]["id"]
    body = client.get(f"/api/v1/employees/{employee_id}/access").json()
    layers = {
        source["layer"]
        for group in ("person", "position", "effective")
        for entry in body[group]
        for source in entry["sources"]
    }
    assert layers <= {"person", "position"}, layers
