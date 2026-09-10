"""T1.0 · 培养子系统基础（docs/cultivation-system-design.md §3）。

锁：建角色全链（person + profile 一体、identity_id 唯一且 CH- 前缀）；
trained 带 template 开 program；blank 自由养成无 program；公司隔离
（A 公司看不到 B 公司的角色）；v27 迁移放开 nullable + 存量无损 + 部分唯一索引保留。
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.repositories import cultivation as cultivation_repo
from app.repositories import persons as person_repo

V26 = "y1f3a5c7e9b2"


def _create(client, name: str, origin: str = "trained", template: str | None = None):
    payload: dict = {"name": name, "origin": origin}
    if template is not None:
        payload["template"] = template
    return client.post("/api/v1/cultivation/characters", json=payload)


def test_create_trained_character_full_chain(client, db):
    response = _create(client, f"Trainee {uuid.uuid4().hex[:6]}", template="academic")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin"] == "trained" and body["lifecycle"] == "cultivating"
    assert body["identity_id"].startswith("CH-") and len(body["identity_id"]) == 15
    assert body["owner_company_id"] is not None  # 当前请求公司

    # person + profile 一体落库；带 template ⇒ program 已开
    person = person_repo.get_person(db, body["person_id"])
    assert person is not None and person.name == body["name"]
    profile = cultivation_repo.get_profile_by_person(db, person.id)
    assert profile is not None and profile.identity_id == body["identity_id"]
    programs = cultivation_repo.list_programs(db, person.id)
    assert len(programs) == 1 and programs[0].template == "academic"
    assert programs[0].rng_seed and programs[0].status == "active"


def test_create_blank_character_has_no_program(client, db):
    response = _create(client, f"Blank {uuid.uuid4().hex[:6]}", origin="blank")
    assert response.status_code == 201, response.text
    body = response.json()
    programs = cultivation_repo.list_programs(db, body["person_id"])
    assert programs == [], "blank 自由养成起点不该自动开 program"


def test_identity_id_is_unique(client, db):
    first = _create(client, f"Dup {uuid.uuid4().hex[:6]}").json()
    profile = cultivation_repo.get_profile(db, first["id"])
    with pytest.raises(IntegrityError):
        db.add(
            type(profile)(
                person_id=-1,
                identity_id=profile.identity_id,
                origin="trained",
                lifecycle="cultivating",
            )
        )
        db.flush()
    db.rollback()


def test_issued_origin_is_rejected_as_t2_scope(client):
    response = _create(client, "Official", origin="issued")
    assert response.status_code == 422
    response = _create(client, "Bad Template", template="moonshot")
    assert response.status_code == 422


def test_characters_are_company_scoped(client, db, default_company_id):
    """A 公司建的角色，B 公司的列表/详情看不到（404/空列表）。"""
    mine = _create(client, f"Mine {uuid.uuid4().hex[:6]}").json()
    assert mine["owner_company_id"] == default_company_id

    other_company_id = default_company_id + 10_000  # 不存在的公司 ⇒ 服务层视角列表为空
    profiles = cultivation_repo.list_characters(db, owner_company_id=other_company_id)
    assert profiles == []

    # 详情跨公司 404（服务层判定）
    from fastapi import HTTPException

    from app.services import cultivation as cultivation_service

    with pytest.raises(HTTPException) as exc:
        cultivation_service.get_character_detail(db, mine["id"], other_company_id)
    assert exc.value.status_code == 404
    # 本公司详情正常：person + program + 事件流
    detail = client.get(f"/api/v1/cultivation/characters/{mine['id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["identity_id"] == mine["identity_id"] and body["events"] == []


def test_list_filter_by_lifecycle(client, db):
    created = _create(client, f"Cult {uuid.uuid4().hex[:6]}").json()
    response = client.get("/api/v1/cultivation/characters", params={"lifecycle": "cultivating"})
    assert response.status_code == 200
    assert all(c["lifecycle"] == "cultivating" for c in response.json())
    # 自包含：把自己养成 ready（引擎跑完模板），再看两个过滤桶的归属
    from app.repositories import cultivation as cultivation_repo

    profile = cultivation_repo.get_profile(db, created["id"])
    profile.lifecycle = "ready"
    db.commit()
    ready = client.get("/api/v1/cultivation/characters", params={"lifecycle": "ready"}).json()
    assert created["id"] in {c["id"] for c in ready}
    cultivating = client.get(
        "/api/v1/cultivation/characters", params={"lifecycle": "cultivating"}
    ).json()
    assert created["id"] not in {c["id"] for c in cultivating}


# ---- 迁移 v27 ----


def test_v27_relaxes_legacy_not_null_and_keeps_data(tmp_path):
    """v27：11 表 employee_id（+learning_sessions.company_id）放开 nullable；
    存量数据无损；命名唯一约束与部分唯一索引重建后原样保留。"""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 't10.db'}")
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, V26)
        # 存量行：一个 employee + 关联 brain/priority
        connection.execute(
            sa.text(
                "INSERT INTO companies (name, slug, description, industry, settings, stage,"
                " created_at, updated_at) VALUES ('Co', 'co', '', '', '{}', 'FOUNDING',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO persons (slug, name, created_at, updated_at)"
                " VALUES ('ada', 'Ada', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO employees (company_id, person_id, name, slug, role, title, avatar,"
                " status, lifecycle_status, username, runtime_type, runtime_config,"
                " workspace_path, memory_namespace, created_at, updated_at) VALUES (1, 1, 'Ada',"
                " 'ada', 'engineer', '', '', 'idle', 'active', 'ada', 'mock', '{}', '/tmp/ada',"
                " 'mem-ada', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO employee_brains (employee_id, person_id, personality, goals,"
                " interests, learning_policy, memory_policy, curiosity, created_at, updated_at)"
                " VALUES (1, 1, '', '', '[]', '{}', '{}', 0.5,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        connection.execute(
            sa.text(
                "INSERT INTO learning_priorities (employee_id, person_id, topic, score, reason,"
                " source, created_at, updated_at) VALUES (1, 1, 't', 70, '', 'failure',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")

    relaxed = {
        "employee_brains": ("employee_id",),
        "runtime_instances": ("employee_id",),
        "memory_entries": ("employee_id",),
        "skills": ("employee_id",),
        "learning_records": ("employee_id",),
        "skill_usages": ("employee_id",),
        "learning_priorities": ("employee_id",),
        "learning_sessions": ("employee_id", "company_id"),
        "employee_competencies": ("employee_id",),
        "competency_evidence": ("employee_id",),
        "assessment_runs": ("employee_id",),
    }
    with engine.connect() as conn:
        for table, columns in relaxed.items():
            nullable = {
                row[1]: not row[3] for row in conn.execute(sa.text(f"PRAGMA table_info({table})"))
            }
            for column in columns:
                assert nullable[column], f"{table}.{column} 仍是 NOT NULL"
        # 存量无损
        assert conn.execute(sa.text("SELECT count(*) FROM employee_brains")).scalar_one() == 1
        assert conn.execute(sa.text("SELECT count(*) FROM learning_priorities")).scalar_one() == 1
        # 命名唯一约束 + 部分唯一索引仍在
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'index'"
                    " AND name = 'uq_learning_priority_person'"
                )
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                sa.text(
                    "SELECT count(*) FROM sqlite_master WHERE type = 'index'"
                    " AND name = 'uq_employee_brains_person'"
                )
            ).scalar_one()
            == 1
        )
        # 三张新表就位
        for table in ("character_profiles", "training_programs", "education_events"):
            assert (
                conn.execute(
                    sa.text(f"SELECT count(*) FROM sqlite_master WHERE name = '{table}'")
                ).scalar_one()
                == 1
            )
        # person-only 行现在可以落了（放开的直接证据；用一个没有 brain 的 person，
        # 避开 uq_employee_brains_person 的合法拦截）
        conn.execute(
            sa.text(
                "INSERT INTO persons (slug, name, created_at, updated_at)"
                " VALUES ('grace', 'Grace', '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO employee_brains (employee_id, person_id, personality, goals,"
                " interests, learning_policy, memory_policy, curiosity, created_at, updated_at)"
                " VALUES (NULL, 2, '', '', '[]', '{}', '{}', 0.5,"
                " '2026-01-02 00:00:00', '2026-01-02 00:00:00')"
            )
        )
