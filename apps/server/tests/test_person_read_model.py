"""T2.1 Person 读面契约（docs/t2-talent-market-design.md §9 / plan §4.2）。

锁：
- **对拍**：同一 person 的 traits / competencies 在 `/persons/{id}` 与
  `/employees/{id}/traits|competencies` 上逐字段一致（读面统一，不允许两套口径）；
- 未评估 = null（**永不 0**）、`trend_direction=unknown`（不假装 STABLE）；
- 无 CharacterProfile 的纯入职员工是合法输入：身份字段如实 null；
- 知识摘要只给 topic/scope/count，**不带正文**（同一形状将被 T2.3 市场投影复用）；
- 公司边界：别家公司的 person（培养持有 或 在职）一律 404，不泄露存在性；
- 时间线倒序（最新在前）。
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import make_employee, make_person, person_id_of

from app.models.competency import CompetencyEvidence
from app.models.organization import Company
from app.repositories import knowledge as knowledge_repo
from app.services import competency as svc

_seq = 0


def _hire(db, company_id: int) -> tuple[int, int]:
    """建员工（自动配 person）→ (employee_id, person_id)。"""
    global _seq
    _seq += 1
    employee = make_employee(
        db,
        company_id=company_id,
        slug=f"t21-{company_id}-{_seq}",
        name=f"T21 Person {_seq}",
        workspace_path=f"/tmp/t21-{company_id}-{_seq}-ws",
        memory_namespace=f"mem-t21-{company_id}-{_seq}",
    )
    db.commit()
    person_id = person_id_of(db, int(employee.id))
    assert person_id is not None
    return int(employee.id), int(person_id)


def _definition_id(db, code: str = "analysis_problem_solving") -> int:
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


def _other_company(db) -> Company:
    seq = db.scalar(sa.text("SELECT count(*) FROM companies"))
    company = Company(name="Rival Co", slug=f"rival-{seq}")
    db.add(company)
    db.flush()
    return company


# ---- 对拍：人员的 traits / competencies 与员工读面一致 ----


def test_person_profile_matches_employee_read_faces(client, db, default_company_id):
    employee_id, person_id = _hire(db, default_company_id)
    db.add(
        CompetencyEvidence(
            employee_id=employee_id,
            person_id=person_id,
            competency_definition_id=_definition_id(db),
            source_kind="review",
            source_ref="REVIEW-1 通过",
            signal=88,
        )
    )
    db.commit()
    svc.assess_employee_competencies(db, employee_id, commit=True)

    profile = client.get(f"/api/v1/persons/{person_id}").json()

    employee_traits = client.get(f"/api/v1/employees/{employee_id}/traits").json()
    employee_caps = client.get(f"/api/v1/employees/{employee_id}/competencies").json()
    assert profile["traits"] == employee_traits
    assert profile["competencies"] == employee_caps
    # 分数确实被评估过（对拍不是"两边都是空"）
    scored = [row for row in profile["competencies"]["general"] if row["score"] is not None]
    assert scored and all(row["confidence"] is not None for row in scored)


def test_person_profile_unrated_is_null_never_zero(client, db, default_company_id):
    _employee_id, person_id = _hire(db, default_company_id)
    body = client.get(f"/api/v1/persons/{person_id}").json()
    assert len(body["competencies"]["general"]) == 10
    for row in body["competencies"]["general"]:
        assert row["score"] is None, f"{row['code']} 未评估不能编 0 分"
        assert row["confidence"] is None
        assert row["evidence_count"] == 0
        assert row["status"] == "unrated"
        assert row["trend"] is None
        assert row["trend_direction"] == "unknown"
    assert body["competencies"]["professional"] == []


def test_person_without_character_profile_has_null_identity_fields(client, db, default_company_id):
    _employee_id, person_id = _hire(db, default_company_id)
    identity = client.get(f"/api/v1/persons/{person_id}").json()["identity"]
    assert identity["person_id"] == person_id
    assert identity["identity_id"] is None  # 没有培养档案 ≠ 错误
    assert identity["origin"] is None
    assert identity["cultivation_state"] is None
    assert identity["owner_company_id"] is None


# ---- 培养角色（person-only）也是合法 person ----


def test_cultivation_character_is_readable_with_timeline(client, db, default_company_id):
    created = client.post(
        "/api/v1/cultivation/characters",
        json={"name": "T21 Cultivar", "origin": "trained", "template": "self_taught"},
    ).json()
    person_id = created["person_id"]

    # include=timeline 之外的区块恒在
    body = client.get(f"/api/v1/persons/{person_id}").json()
    assert body["identity"]["identity_id"] == created["identity_id"]
    assert body["identity"]["cultivation_state"] == "cultivating"
    assert body["timeline"] is None and body["evidence"] is None

    program_id = client.get(f"/api/v1/cultivation/characters/{created['id']}").json()["programs"][
        0
    ]["id"]
    assert client.post(f"/api/v1/cultivation/programs/{program_id}/advance").status_code == 200

    with_include = client.get(
        f"/api/v1/persons/{person_id}", params={"include": "timeline,evidence"}
    ).json()
    assert with_include["timeline"], "推进一阶段后履历应有事件"
    assert with_include["evidence"], "培养产出应有教育证据"
    assert with_include["evidence"][0]["person_id"] == person_id
    assert "employee_id" not in with_include["evidence"][0]


def test_person_timeline_endpoint_is_newest_first(client, db, default_company_id):
    created = client.post(
        "/api/v1/cultivation/characters",
        json={"name": "T21 Timeline", "origin": "blank"},
    ).json()
    person_id = created["person_id"]
    for topic in ("第一个主题", "第二个主题"):
        assert (
            client.post(
                f"/api/v1/cultivation/characters/{created['id']}/sessions",
                json={"topic": topic, "mode": "web_research", "kind": "course", "signal": 60},
            ).status_code
            == 201
        )

    timeline = client.get(f"/api/v1/persons/{person_id}/timeline").json()
    assert [row["topic"] for row in timeline] == ["第二个主题", "第一个主题"]

    # include=timeline 与子资源端点同源同序
    included = client.get(f"/api/v1/persons/{person_id}", params={"include": "timeline"}).json()[
        "timeline"
    ]
    assert [row["id"] for row in included] == [row["id"] for row in timeline]


def test_person_evidence_endpoint_filters_by_source_and_competency(client, db, default_company_id):
    created = client.post(
        "/api/v1/cultivation/characters",
        json={"name": "T21 Evidence", "origin": "blank"},
    ).json()
    person_id = created["person_id"]
    client.post(
        f"/api/v1/cultivation/characters/{created['id']}/sessions",
        json={"topic": "证据主题", "mode": "document_study", "kind": "project", "signal": 75},
    )

    rows = client.get(f"/api/v1/persons/{person_id}/evidence").json()
    assert len(rows) == 1
    assert rows[0]["person_id"] == person_id
    assert rows[0]["source_kind"] == "edu_project"
    assert rows[0]["signal"] == 75
    assert rows[0]["competency_code"] == "analysis_problem_solving"

    assert (
        client.get(
            f"/api/v1/persons/{person_id}/evidence", params={"source_type": "edu_course"}
        ).json()
        == []
    )
    assert (
        len(
            client.get(
                f"/api/v1/persons/{person_id}/evidence",
                params={"competency": "analysis_problem_solving"},
            ).json()
        )
        == 1
    )
    assert (
        client.get(
            f"/api/v1/persons/{person_id}/evidence", params={"competency": "no_such_code"}
        ).status_code
        == 404
    )


# ---- 知识摘要：只有统计，没有正文 ----


def test_person_knowledge_summary_has_counts_without_content(client, db, default_company_id):
    _employee_id, person_id = _hire(db, default_company_id)
    secret = "机密正文-不应出现在市场投影里"
    for topic, scope in (("分布式系统", "private"), ("分布式系统", "private"), ("安全", "company")):
        knowledge_repo.create_knowledge_item(
            db,
            owner_person_id=person_id,
            topic=topic,
            title=f"{topic} 标题",
            content=secret,
            scope=scope,
        )
    db.commit()

    summary = client.get(f"/api/v1/persons/{person_id}").json()["knowledge_summary"]
    assert summary["total"] == 3
    assert summary["by_scope"] == {"private": 2, "company": 1}
    assert summary["top_topics"][0] == {"topic": "分布式系统", "count": 2}

    raw = client.get(f"/api/v1/persons/{person_id}").text
    assert secret not in raw, "知识正文不得进入人员读面（同一形状将供市场投影复用）"
    assert "标题" not in raw


# ---- 公司边界与参数校验 ----


def test_person_read_faces_hide_other_company_persons(client, db, default_company_id):
    rival = _other_company(db)
    # 别家公司的培养角色
    rival_profile_person = make_person(db, slug=f"rival-character-{rival.id}")
    db.execute(
        sa.text(
            "INSERT INTO character_profiles (person_id, identity_id, origin, owner_company_id,"
            " lifecycle, created_at, updated_at)"
            " VALUES (:pid, :iid, 'trained', :cid, 'ready',"
            " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
        ),
        {"pid": rival_profile_person.id, "iid": f"CH-RIVAL{rival.id:06d}", "cid": rival.id},
    )
    # 别家公司的在职员工
    rival_employee = make_employee(db, company_id=rival.id, slug=f"rival-emp-{rival.id}")
    db.commit()

    assert client.get(f"/api/v1/persons/{rival_profile_person.id}").status_code == 404
    assert client.get(f"/api/v1/persons/{rival_employee.person_id}").status_code == 404
    assert client.get("/api/v1/persons/999999").status_code == 404
    assert client.get(f"/api/v1/persons/{rival_employee.person_id}/timeline").status_code == 404
    assert client.get(f"/api/v1/persons/{rival_employee.person_id}/evidence").status_code == 404


def test_person_profile_rejects_unknown_include(client, db, default_company_id):
    _employee_id, person_id = _hire(db, default_company_id)
    response = client.get(f"/api/v1/persons/{person_id}", params={"include": "wallet"})
    assert response.status_code == 422
    assert "unknown include" in response.json()["detail"]


# ---- 读模型保持只读（结构守卫，防止 T2.7 把写逻辑塞进读面） ----


def _person_read_model_files():
    from pathlib import Path

    server_root = Path(__file__).resolve().parents[1]
    return [
        (path, str(path.relative_to(server_root)))
        for path in sorted((server_root / "app" / "talent" / "person").rglob("*.py"))
        if "__pycache__" not in path.parts
    ]


def test_person_read_model_does_not_write():
    """人员读面不得落库：没有 add/commit/flush/delete（读模型不是第二写入口）。"""
    import ast

    forbidden = {"add", "commit", "flush", "delete"}
    offenders: list[str] = []
    for path, relative in _person_read_model_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden and isinstance(node.func.value, ast.Name):
                    if node.func.value.id == "db":
                        offenders.append(f"{relative}:{node.lineno} db.{node.func.attr}()")
    assert not offenders, "人员读面必须只读：\n" + "\n".join(f"  - {item}" for item in offenders)


def test_person_read_model_does_not_import_write_paths():
    """读面不得 import 评估/学习/培养引擎等写路径（复用读出口，不触发产出）。"""
    import ast

    forbidden_prefixes = (
        "app.services.assessment",
        "app.services.learning",
        "app.evidence.normalize",
        "app.talent.cultivation.engine",
    )
    offenders: list[str] = []
    for path, relative in _person_read_model_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                if module.startswith(forbidden_prefixes):
                    offenders.append(f"{relative}:{node.lineno} {module}")
    assert not offenders, "人员读面不得依赖写路径：\n" + "\n".join(
        f"  - {item}" for item in offenders
    )
