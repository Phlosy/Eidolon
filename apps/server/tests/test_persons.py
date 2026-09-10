"""PersonCore 兼容层（R1.0，docs/person-core-migration.md §3 D2/D4.1、§4 R1.0）。

锁：
- repositories/persons.py 原语（create/get/by_slug）与 resolve_person_id 单一解析入口；
- onboard 双写：先建 person 再建 employee，字段同源镜像、person_id 回填、1-1 relationship；
- seed 双写：演示 workforce 的每个 employee 都有配对 person；
- employees.person_id 部分唯一索引真的拒绝「一个 person 挂两个 employee」；
- 迁移 v21 内联回填：每个历史 employee 生成一个 person、字段照抄、时间戳保留、按 slug 回连。
"""

import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from factories import make_employee, make_person
from sqlalchemy.exc import IntegrityError

from app.core.database import _alembic_config
from app.models.organization import Company, Employee
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo

V20 = "s5a7c9e1f3b5"
V21 = "t6a8c0e2f4b6"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


# ---- repositories/persons.py 原语 ----


def test_person_repo_create_get_and_slug_lookup(db):
    person = make_person(db, slug=_unique("person"), name="Standalone", username="standalone")
    db.commit()

    assert person.id is not None
    assert person_repo.get_person(db, person.id).slug == person.slug
    assert person_repo.get_person_by_slug(db, person.slug).id == person.id
    assert person_repo.get_person_by_slug(db, _unique("nobody")) is None
    assert person.avatar == ""


def test_person_slug_is_globally_unique(db):
    slug = _unique("dupe")
    make_person(db, slug=slug)
    db.commit()
    with pytest.raises(IntegrityError):  # 工厂内部 flush，冲突在工厂里就炸
        make_person(db, slug=slug)
    db.rollback()


def test_resolve_person_id_is_the_only_translation_door(db, default_company_id):
    employee = make_employee(db, company_id=default_company_id, slug=_unique("resolve"))
    db.commit()

    assert person_repo.resolve_person_id(db, employee.id) == employee.person_id
    assert person_repo.resolve_person_id(db, 10**9) is None


def test_one_person_cannot_back_two_employees(db, default_company_id):
    """部分唯一索引 uq_employees_person_id：兼容期 Employee 1-1 代理 Person 的完整性地基。"""
    person = make_person(db, slug=_unique("shared"))
    make_employee(db, company_id=default_company_id, slug=_unique("first"), person=person)
    db.commit()
    with pytest.raises(IntegrityError):  # 第二个 employee 落库时撞上部分唯一索引
        make_employee(db, company_id=default_company_id, slug=_unique("second"), person=person)
    db.rollback()


# ---- 写入口双写（D2）----


def test_onboard_double_writes_person(db, client):
    """入职 = 先建 person 再建 employee：字段同源镜像、person_id 回填、1-1 互通。"""
    slug = _unique("onboard")
    company = org_repo.get_default_company(db)
    department = org_repo.get_department_by_slug(db, company.id, "engineering")
    response = client.post(
        "/api/v1/employees/onboard",
        json={
            "name": "Person Hire",
            "slug": slug,
            "title": "Engineer",
            "role": "engineer",
            "department_id": department.id,
            "runtime_type": "mock",
        },
    )
    assert response.status_code == 201, response.text

    employee = db.get(Employee, response.json()["employee"]["id"])
    person = person_repo.get_person_by_slug(db, slug)
    assert person is not None
    assert employee.person_id == person.id
    # 镜像字段一致：兼容期 employees.* 是镜像，persons.* 是权威
    assert (person.name, person.avatar, person.username) == (
        employee.name,
        employee.avatar,
        employee.username,
    )
    assert employee.person is person
    assert person.employee is employee


def test_seed_workforce_employees_all_have_persons(db):
    """seed 双写：演示 workforce（alice/bob/…）人人都有配对 person，字段镜像一致。"""
    seeded = (
        db.query(Employee)
        .filter(Employee.slug.in_(["alice", "morgan", "bob", "charlie", "dana"]))
        .all()
    )
    assert len(seeded) == 5, "演示 workforce 应已由启动 seed 落库"
    for employee in seeded:
        person = person_repo.get_person(db, employee.person_id) if employee.person_id else None
        assert person is not None, f"{employee.slug} 缺 person"
        assert (person.slug, person.name, person.avatar, person.username) == (
            employee.slug,
            employee.name,
            employee.avatar,
            employee.username,
        )


# ---- 迁移 v21 内联回填 ----


def _upgrade(engine, revision: str) -> None:
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def _seed_v20_employees(engine) -> None:
    """在 v20 schema 上插历史行（raw SQL：当前模型已带 person_id，不能用它写旧 schema）。"""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (name, slug, description, industry, settings, stage,"
                " created_at, updated_at) VALUES ('Legacy Co', 'legacy-co', '', '', '{}',"
                " 'FOUNDING', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        company_id = conn.execute(sa.text("SELECT id FROM companies")).scalar_one()
        for slug, name, username in (
            ("legacy-ada", "Ada", "legacy-ada"),
            ("legacy-grace", "Grace", None),  # username 可空：老种子形态
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO employees (company_id, name, slug, role, title, avatar, status,"
                    " lifecycle_status, username, runtime_type, runtime_config, workspace_path,"
                    " memory_namespace, created_at, updated_at) VALUES (:cid, :name, :slug,"
                    " 'engineer', '', '', 'idle', 'active', :username, 'mock', '{}', :ws, :mem,"
                    " '2026-01-01 00:00:00', '2026-01-02 00:00:00')"
                ),
                {
                    "cid": company_id,
                    "name": name,
                    "slug": slug,
                    "username": username,
                    "ws": f"/tmp/{slug}-ws",
                    "mem": f"mem-{slug}",
                },
            )


def test_v21_backfills_a_person_for_every_employee(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'backfill.db'}")
    _upgrade(engine, V20)
    _seed_v20_employees(engine)

    _upgrade(engine, "head")

    with engine.connect() as conn:
        employees = conn.execute(
            sa.text(
                "SELECT slug, name, avatar, username, person_id, created_at, updated_at"
                " FROM employees ORDER BY id"
            )
        ).all()
        persons = {
            row.slug: row
            for row in conn.execute(
                sa.text(
                    "SELECT id, slug, name, avatar, username, created_at, updated_at FROM persons"
                )
            )
        }
        assert len(employees) == 2 and len(persons) == 2
        for slug, name, avatar, username, person_id, created_at, updated_at in employees:
            assert person_id is not None
            person = persons[slug]
            assert person.id == person_id
            # 字段照抄（含 username=None 的形态）、时间戳原样保留
            assert (person.name, person.avatar, person.username) == (name, avatar, username)
            assert person.created_at == created_at and person.updated_at == updated_at
        # 部分唯一索引真实存在（WHERE person_id IS NOT NULL）
        index_sql = conn.execute(
            sa.text(
                "SELECT sql FROM sqlite_master"
                " WHERE type = 'index' AND name = 'uq_employees_person_id'"
            )
        ).scalar_one()
        assert "WHERE" in index_sql.upper() and "person_id IS NOT NULL" in index_sql


def test_v21_downgrade_removes_person_core(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'downgrade.db'}")
    _upgrade(engine, "head")
    with engine.begin() as connection:
        config = _alembic_config()
        config.attributes["connection"] = connection
        command.downgrade(config, V20)

    with engine.connect() as conn:
        tables = set(conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'table'")))
        assert "persons" not in tables
        columns = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(employees)"))}
        assert "person_id" not in columns


# ===========================================================================
# R1.1 批次 1：人格与学习域切读（docs/person-core-migration.md §3 D4）
# ===========================================================================

from app.repositories import knowledge as knowledge_repo  # noqa: E402
from app.repositories import runtimes as runtime_repo  # noqa: E402
from app.services import learning as learning_service  # noqa: E402


def test_knowledge_repo_double_writes_employee_and_person(db, default_company_id):
    """双写：5 张表的 create 路径同时落 employee_id（镜像）与 person_id（权威）。"""
    employee = make_employee(db, company_id=default_company_id, slug=_unique("dw"))
    db.commit()
    person_id = employee.person_id

    entry = knowledge_repo.create_memory_entry(db, employee.id, kind="note", content="x")
    skill = knowledge_repo.create_skill(db, employee_id=employee.id, name="python")
    usage = knowledge_repo.create_skill_usage(
        db, employee_id=employee.id, skill_id=skill.id, task_id=None, selection_reason="manual"
    )
    record = knowledge_repo.create_learning_record(db, employee_id=employee.id, topic="t")
    priority = knowledge_repo.create_learning_priority(
        db, employee_id=employee.id, topic="t", score=70, source="failure"
    )
    for row in (entry, skill, usage, record, priority):
        assert row.employee_id == employee.id, "deprecated 镜像列必须继续写"
        assert row.person_id == person_id, "权威口径列必须双写"


def test_brain_ensure_double_writes_and_get_reads_person(db, default_company_id):
    employee = make_employee(db, company_id=default_company_id, slug=_unique("brain"))
    db.commit()

    brain = runtime_repo.ensure_brain(db, employee.id)
    assert brain.employee_id == employee.id
    assert brain.person_id == employee.person_id
    assert runtime_repo.get_brain(db, employee.id).id == brain.id
    # 一人重复 ensure 幂等（读走 person 口径仍找得到）
    assert runtime_repo.ensure_brain(db, employee.id).id == brain.id


def test_learning_session_create_double_writes_person(db, default_company_id):
    employee = make_employee(
        db, company_id=default_company_id, slug=_unique("ls"), lifecycle_status="active"
    )
    db.commit()
    session = learning_service.create_session(db, employee.id, "topic-x", commit=False)
    assert session.employee_id == employee.id
    assert session.person_id == employee.person_id


def test_reads_fall_back_to_employee_id_when_person_unresolvable(db, default_company_id, caplog):
    """方案 §5 对策：resolve 不到 person_id ⇒ 回落 employee_id 旧口径 + warning。"""
    legacy = Employee(  # 测试里裸构造一个无 person 的 legacy 员工（迁移前形态）
        company_id=default_company_id,
        name="Legacy",
        slug=_unique("legacy"),
        workspace_path=f"/tmp/{_unique('lg')}-ws",
        memory_namespace=f"mem-{_unique('lg')}",
    )
    db.add(legacy)
    db.commit()
    assert legacy.person_id is None

    with caplog.at_level("WARNING", logger="app.repositories.persons"):
        knowledge_repo.create_memory_entry(db, legacy.id, kind="note", content="old")
        entries = knowledge_repo.list_memory_entries(db, legacy.id)
        skills = knowledge_repo.list_skills(db, legacy.id)
        brain = runtime_repo.get_brain(db, legacy.id)
    assert [e.content for e in entries] == ["old"], "回落旧口径必须读到 person_id 为 NULL 的行"
    assert skills == [] and brain is None
    assert caplog.messages, "回落必须留 warning 痕迹"


def test_read_criterion_uses_person_column_when_resolved(db, default_company_id):
    """切读语义钉死：行里 employee_id 镜像即使失真，person 口径照样读到（权威在 person_id）。"""
    employee = make_employee(db, company_id=default_company_id, slug=_unique("auth"))
    db.commit()
    entry = knowledge_repo.create_memory_entry(
        db, employee.id, kind="note", content="authoritative"
    )
    # 模拟镜像列失真（兼容期两列并存，权威是 person_id）
    db.execute(
        sa.text("UPDATE memory_entries SET employee_id = -1 WHERE id = :id"), {"id": entry.id}
    )
    db.commit()
    assert [e.content for e in knowledge_repo.list_memory_entries(db, employee.id)] == [
        "authoritative"
    ]


def test_v22_backfills_person_id_on_all_batch1_tables(tmp_path):
    """v22 内联回填：7 张表 person_id 非空率 100%，部分唯一索引就位。"""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'batch1.db'}")
    _upgrade(engine, V21)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (name, slug, description, industry, settings, stage,"
                " created_at, updated_at) VALUES ('Co', 'co', '', '', '{}', 'FOUNDING',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO persons (slug, name, avatar, username, created_at, updated_at)"
                " VALUES ('ada', 'Ada', '', 'ada', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO employees (company_id, name, slug, role, title, avatar, status,"
                " lifecycle_status, username, runtime_type, runtime_config, workspace_path,"
                " memory_namespace, person_id, created_at, updated_at) VALUES (1, 'Ada', 'ada',"
                " 'engineer', '', '', 'idle', 'active', 'ada', 'mock', '{}', '/tmp/ada',"
                " 'mem-ada', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO employee_brains (employee_id, personality, goals, interests,"
                " learning_policy, memory_policy, curiosity, created_at, updated_at) VALUES"
                " (1, '', '', '[]', '{}', '{}', 0.5, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO memory_entries (employee_id, kind, content, source_ref, created_at,"
                " updated_at) VALUES (1, 'note', 'c', '', '2026-01-01 00:00:00',"
                " '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO skills (employee_id, name, description, version, attempts,"
                " success_count, avg_duration_sec, avg_cost, validation_status, created_at,"
                " updated_at) VALUES (1, 'python', '', 1, 0, 0, 0.0, 0.0, 'candidate',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO learning_records (employee_id, kind, topic, problem, observation,"
                " lesson, solution, confidence, sources, created_at, updated_at) VALUES"
                " (1, 'reflection', 't', '', '', '', '', 0.0, '[]', '2026-01-01 00:00:00',"
                " '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO skill_usages (employee_id, skill_id, skill_validation_status,"
                " selection_reason, policy_version, profile_revision, success, created_at,"
                " updated_at) VALUES (1, 1, 'candidate', 'manual', '', 0, 0,"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO learning_priorities (employee_id, topic, score, reason, source,"
                " created_at, updated_at) VALUES (1, 't', 70, '', 'failure',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO learning_sessions (company_id, employee_id, topic, reason,"
                " source_type, status, learning_mode, priority, budget_tokens, budget_cost,"
                " budget_minutes, tokens_used, cost_used, runtime_type, provider_name,"
                " model_name, error, summary, metadata_json, created_at, updated_at) VALUES"
                " (1, 1, 't', '', 'manual', 'completed', 'web_research', 0, 0, 0.0, 0, 0, 0.0,"
                " 'mock', '', '', '', '', '{}', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    _upgrade(engine, "head")

    tables = (
        "employee_brains",
        "memory_entries",
        "skills",
        "learning_records",
        "learning_sessions",
        "skill_usages",
        "learning_priorities",
    )
    with engine.connect() as conn:
        for table in tables:
            total = conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()
            null = conn.execute(
                sa.text(f"SELECT count(*) FROM {table} WHERE person_id IS NULL")
            ).scalar_one()
            assert total == 1 and null == 0, f"{table} 回填遗漏"
            assert conn.execute(sa.text(f"SELECT person_id FROM {table}")).scalar_one() == 1
        for index_name in ("uq_employee_brains_person", "uq_learning_priority_person"):
            index_sql = conn.execute(
                sa.text("SELECT sql FROM sqlite_master WHERE type = 'index' AND name = :name"),
                {"name": index_name},
            ).scalar_one()
            assert "WHERE" in index_sql.upper() and "person_id IS NOT NULL" in index_sql


# ===========================================================================
# R1.2 批次 2：knowledge_items 属主口径（docs/person-core-migration.md §3 D4）
# ===========================================================================


V22 = "u7b9d1f3a5c8e"


def test_knowledge_item_double_writes_owner_employee_and_person(db, default_company_id):
    """双写：owner_employee_id 镜像 + owner_person_id 权威；无 owner 的条目跳过解析。"""
    employee = make_employee(db, company_id=default_company_id, slug=_unique("ki"))
    db.commit()

    item = knowledge_repo.create_knowledge_item(
        db, scope="private", owner_employee_id=employee.id, title="t", topic="t"
    )
    assert item.owner_employee_id == employee.id
    assert item.owner_person_id == employee.person_id

    shared = knowledge_repo.create_knowledge_item(
        db, scope="company", department_id=None, title="c", topic="t"
    )
    assert shared.owner_employee_id is None and shared.owner_person_id is None


def test_private_knowledge_owner_read_uses_person_column(db, default_company_id):
    """切读：private 过滤按 owner_person_id；镜像列失真不影响读取（权威在 person）。"""
    employee = make_employee(db, company_id=default_company_id, slug=_unique("kp"))
    db.commit()
    item = knowledge_repo.create_knowledge_item(
        db, scope="private", owner_employee_id=employee.id, title="mine", topic="t"
    )
    db.execute(
        sa.text("UPDATE knowledge_items SET owner_employee_id = -1 WHERE id = :id"),
        {"id": item.id},
    )
    db.commit()
    items = knowledge_repo.list_knowledge_items(db, scope="private", employee_id=employee.id)
    assert [i.title for i in items] == ["mine"]


def test_private_knowledge_isolation_survives_person_switch(db, default_company_id):
    """隔离不变量：别人的 private 读不到 —— 切 person 口径后这条铁律不变（§3.4.1）。"""
    owner = make_employee(db, company_id=default_company_id, slug=_unique("kown"))
    other = make_employee(db, company_id=default_company_id, slug=_unique("koth"))
    knowledge_repo.create_knowledge_item(
        db, scope="private", owner_employee_id=owner.id, title="secret", topic="t"
    )
    db.commit()
    assert knowledge_repo.list_knowledge_items(db, scope="private", employee_id=other.id) == []
    mine = knowledge_repo.list_knowledge_items(db, scope="private", employee_id=owner.id)
    assert [i.title for i in mine] == ["secret"]


def test_knowledge_company_isolation_still_via_employee_membership(db, default_company_id):
    """公司边界仍由 employees 成员身份提供（persons 无 company_id），切读后语义不变。"""
    other_company = Company(name="Other Co", slug=_unique("other-co"), description="")
    db.add(other_company)
    db.flush()
    outsider = make_employee(db, company_id=other_company.id, slug=_unique("kout"))
    mine = make_employee(db, company_id=default_company_id, slug=_unique("kin"))
    knowledge_repo.create_knowledge_item(
        db, scope="private", owner_employee_id=outsider.id, title="outside", topic="t"
    )
    knowledge_repo.create_knowledge_item(
        db, scope="private", owner_employee_id=mine.id, title="inside", topic="t"
    )
    db.commit()
    items = knowledge_repo.list_knowledge_items(
        db, scope="private", employee_id=mine.id, company_id=default_company_id
    )
    assert [i.title for i in items] == ["inside"]


def test_v23_backfills_owner_person_id_and_skips_null_owner(tmp_path):
    """v23 内联回填：有 owner 的行回填 owner_person_id；NULL owner（scope 分层条目）跳过。"""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'batch2.db'}")
    _upgrade(engine, V22)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO companies (name, slug, description, industry, settings, stage,"
                " created_at, updated_at) VALUES ('Co', 'co', '', '', '{}', 'FOUNDING',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO persons (slug, name, avatar, username, created_at, updated_at)"
                " VALUES ('ada', 'Ada', '', 'ada', '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO employees (company_id, name, slug, role, title, avatar, status,"
                " lifecycle_status, username, runtime_type, runtime_config, workspace_path,"
                " memory_namespace, person_id, created_at, updated_at) VALUES (1, 'Ada', 'ada',"
                " 'engineer', '', '', 'idle', 'active', 'ada', 'mock', '{}', '/tmp/ada',"
                " 'mem-ada', 1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
        # 有属主的 private 条目（应回填）+ 无属主的 company 条目（应保持 NULL）
        conn.execute(
            sa.text(
                "INSERT INTO knowledge_items (scope, owner_employee_id, title, content, topic,"
                " status, confidence, sources, freshness_status, created_at, updated_at) VALUES"
                " ('private', 1, 'owned', '', 't', 'active', 0.0, '[]', 'fresh',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00'),"
                " ('company', NULL, 'shared', '', 't', 'active', 0.0, '[]', 'fresh',"
                " '2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )

    _upgrade(engine, "head")

    with engine.connect() as conn:
        owned = conn.execute(
            sa.text("SELECT owner_person_id FROM knowledge_items WHERE title = 'owned'")
        ).scalar_one()
        shared = conn.execute(
            sa.text("SELECT owner_person_id FROM knowledge_items WHERE title = 'shared'")
        ).scalar_one()
        assert owned == 1, "有 owner 的行必须回填 owner_person_id"
        assert shared is None, "NULL owner 的行必须保持 NULL"
        index = conn.execute(
            sa.text(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
                " AND name = 'ix_knowledge_items_owner_person_id'"
            )
        ).scalar_one_or_none()
        assert index is not None
