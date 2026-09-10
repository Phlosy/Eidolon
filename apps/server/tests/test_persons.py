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
from app.models.organization import Employee
from app.repositories import organization as org_repo
from app.repositories import persons as person_repo

V20 = "s5a7c9e1f3b5"


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
