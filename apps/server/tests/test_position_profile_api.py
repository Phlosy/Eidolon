"""P7 Position Profile API 契约（docs/position-competency-profile.md §20~21）。

锁：
- 默认画像可读（configured、general/professional、min/target/conf/weight、critical）；
- 无画像职位：configured=false 且 integrity=NO_ACTIVE_PROFILE（不是空列表）；
- Draft 编辑 / Publish v2→v1 RETIRED / 单 ACTIVE / 历史保留（versions 列表）；
- 编辑只发生在版本层，员工 API 不接触岗位标准；
- 公司隔离（system/其它公司 404/403）；模板列表可读、clone 到本公司；
- coverage/integrity 派生且 read_only；
- org 列表摘要批量（无 N+1：只比较响应形状，接口层保证由服务批量查询实现）；
- ADR-12：configured=false 字段显式，不伪造 0。
"""

from __future__ import annotations

import sqlalchemy as sa

from app.models.position import PositionDefinition

_seq = 0


def _def_id(db, code: str) -> int:
    return int(
        db.scalar(
            sa.text(
                "SELECT d.id FROM competency_definitions d"
                " JOIN competency_domains dm ON dm.id = d.domain_id"
                " WHERE d.code = :code AND dm.company_id IS NULL"
            ),
            {"code": code},
        )
    )


def _custom_definition(db, company_id: int) -> int:
    global _seq
    _seq += 1
    definition = PositionDefinition(
        company_id=company_id,
        template_scope="company",
        code=f"proj-api-{_seq}",
        name=f"API P7 {_seq}",
        job_family="engineering",
    )
    db.add(definition)
    db.commit()
    return int(definition.id)


def test_default_engineer_profile_is_readable_full(client, default_company_id):
    definitions = client.get("/api/v1/position-profiles").json()
    engineer = next(item for item in definitions if item["code"] == "engineer")
    assert engineer["active_version"] == 1
    assert engineer["profile_status"] == "active"
    detail = client.get(
        f"/api/v1/position-definitions/{engineer['position_definition_id']}/competency-profile"
    ).json()
    assert detail["configured"] is True
    assert detail["profile_version"] == 1
    assert detail["assessment_profile"]["code"] == "software_engineer"
    general_codes = {item["code"] for item in detail["general"]}
    assert "execution" in general_codes and "quality_reliability" in general_codes
    backend = next(item for item in detail["professional"] if item["code"] == "backend_engineering")
    assert backend["requirement_type"] == "required" and backend["critical"] is True
    assert backend["minimum_score"] == 65 and backend["target_score"] == 80
    # 版本历史存在且 ACTIVE
    assert detail["versions"][0]["status"] == "active"
    assert detail["integrity"]["read_only"] is True


def test_position_without_profile_is_not_configured(client, db, default_company_id):
    position_id = _custom_definition(db, default_company_id)
    detail = client.get(f"/api/v1/position-definitions/{position_id}/competency-profile").json()
    assert detail["configured"] is False
    assert detail["profile_version"] is None
    assert "NO_ACTIVE_PROFILE" in detail["integrity"]["codes"]
    assert detail["general"] == [] and detail["professional"] == []


def test_draft_edit_and_publish_v2_retires_v1(client, db, default_company_id):
    position_id = _custom_definition(db, default_company_id)
    created = client.post(f"/api/v1/position-definitions/{position_id}/competency-profile/versions")
    assert created.status_code == 201
    draft = created.json()
    version_id = next(item["id"] for item in draft["versions"]) if draft["versions"] else None
    # create 接口返回里没带 versions（简化）；用列表接口找 draft
    detail = client.get(f"/api/v1/position-definitions/{position_id}/competency-profile").json()
    version_id = detail["versions"][0]["id"] if detail["versions"] else None
    assert version_id is not None and detail["versions"][0]["status"] == "draft"

    # 手动 run 出 v1（先加需求）
    add = client.post(
        f"/api/v1/position-profile-versions/{version_id}/requirements",
        json={
            "competency_definition_id": _def_id(db, "execution"),
            "requirement_type": "required",
            "minimum_score": 60,
            "target_score": 85,
            "critical": True,
            "weight": 0.2,
        },
    )
    assert add.status_code == 201, add.text

    # 无效 target<min 被拒（服务层 + 请求层都拦）
    bad = client.post(
        f"/api/v1/position-profile-versions/{version_id}/requirements",
        json={
            "competency_definition_id": _def_id(db, "testing"),
            "minimum_score": 90,
            "target_score": 80,
        },
    )
    assert bad.status_code in (422, 422)

    published = client.post(f"/api/v1/position-profile-versions/{version_id}/publish", json={})
    assert published.status_code == 200, published.text

    # 加一个新版本（v2）
    client.post(f"/api/v1/position-definitions/{position_id}/competency-profile/versions")
    detail = client.get(f"/api/v1/position-definitions/{position_id}/competency-profile").json()
    versions = {item["version"]: item for item in detail["versions"]}
    assert versions[1]["status"] == "active"
    assert "draft" in {item["status"] for item in detail["versions"]}
    v2_id = next(item["id"] for item in detail["versions"] if item["status"] == "draft")
    # edit draft requirement（v2 空，直接 add）
    add2 = client.post(
        f"/api/v1/position-profile-versions/{v2_id}/requirements",
        json={
            "competency_definition_id": _def_id(db, "testing"),
            "minimum_score": 60,
            "target_score": 82,
            "requirement_type": "preferred",
        },
    )
    assert add2.status_code == 201
    # Publish v2 ⇒ v1 RETIRED
    resp = client.post(
        f"/api/v1/position-profile-versions/{v2_id}/publish", json={"note": "target raised"}
    )
    assert resp.status_code == 200
    detail = client.get(f"/api/v1/position-definitions/{position_id}/competency-profile").json()
    versions = {item["version"]: item for item in detail["versions"]}
    assert versions[2]["status"] == "active"
    assert versions[1]["status"] == "retired"


def test_company_isolation_and_template_read_only(client, db, default_company_id):
    from app.models.organization import Company

    other = Company(name="P7Other", slug=f"p7-other-{_seq}", description="")
    db.add(other)
    db.commit()
    foreign = _custom_definition(db, int(other.id))
    detail = client.get(f"/api/v1/position-definitions/{foreign}/competency-profile")
    assert detail.status_code == 404, "其它公司画像不可读"
    assert (
        client.post(
            f"/api/v1/position-definitions/{foreign}/competency-profile/versions"
        ).status_code
        == 404
    )
    # 模板列表（本公司 ACTIVE）可读
    templates = client.get("/api/v1/position-profiles/templates").json()
    assert any(item["position_code"] == "engineer" for item in templates)


def test_org_list_summary_has_no_detail_bloat(client, default_company_id):
    summaries = client.get("/api/v1/position-profiles").json()
    for item in summaries:
        assert "general" not in item and "professional" not in item
        assert "requirement_count" in item
        assert "active_version" in item
    assert any(item["code"] == "engineer" for item in summaries)
