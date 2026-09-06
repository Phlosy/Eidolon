"""v12–v14 迁移的历史保留验收（docs/workforce-domain-refactor.md §6）。

为什么单独一个文件：这次重构最大的风险不是新表建不出来，而是**把已有的人和历史弄坏**。
所以这里刻意混合两种写法：
  * 人侧（company/department/employee/brain/runtime/package）用 ORM 造，
    因为那些模型与 v11 schema 一致；
  * `positions` / `employments` 用裸 SQL 造 v0.4 形态的脏数据，
    因为当前实体已经带 v14 新列，用 ORM 就测不到"从旧形状升上来"这件事。

断言四类：
  A 人的数据逐项不变（employee / brain / runtime / namespace / workspace）
  B 任职历史不删行、已关闭的时间窗不被改写
  C 回填正确（老任职找到坑、头衔快照、孤儿行留痕而不是被丢弃）
  D 清洗与约束（重复主职只留最新并 superseded+留痕；部分唯一索引真的拦得住）
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from alembic import command
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.core.database import _alembic_config
from app.models.enums import EmployeeStatus, LifecycleStatus, RuntimeType
from app.models.lifecycle import AccessPackage
from app.models.organization import Company, Department, Employee
from app.models.runtime import EmployeeBrain, RuntimeInstance

V11 = "j5e8a1b4c730"
EMPLOYMENT_COLUMNS_ADDED = (
    "position_slot_id",
    "assignment_type",
    "is_primary",
    "assigned_by",
    "reason",
    "position_title_snapshot",
)


def _engine(tmp_path) -> Engine:
    return sa.create_engine(f"sqlite:///{tmp_path / 'history.db'}")


def _run(engine: Engine, action: str, revision: str) -> None:
    config = _alembic_config()
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        getattr(command, action)(config, revision)


def _seed_legacy(engine: Engine) -> datetime:
    now = datetime.now(UTC)
    with Session(engine) as session:
        company = Company(name="Eidolon Studio", slug="eidolon", description="", industry="")
        session.add(company)
        session.flush()
        engineering = Department(company_id=company.id, name="Engineering", slug="engineering")
        research = Department(company_id=company.id, name="Research", slug="research")
        session.add_all([engineering, research])
        session.flush()

        for slug, dept, role in (
            ("charlie", engineering, "engineer"),
            ("dana", engineering, "qa_engineer"),
            ("eve", research, "researcher"),
            # frank：dev 库里的多数形态 —— 19 条任职有 14 条 position_id 为空，
            # 即老代码建任职时根本没关联职位（真相在 employees.role 文本上）。
            ("frank", research, "engineer"),
        ):
            employee = Employee(
                company_id=company.id,
                department_id=dept.id,
                name=slug.title(),
                slug=slug,
                role=role,
                title="t",
                status=EmployeeStatus.idle.value,
                lifecycle_status=LifecycleStatus.active.value,
                username=slug,
                runtime_type=RuntimeType.mock.value,
                runtime_config={},
                workspace_path=f"data/employees/{slug}",
                memory_namespace=f"emp_{slug}",
            )
            session.add(employee)
        session.flush()

        charlie = session.scalar(sa.select(Employee).where(Employee.slug == "charlie"))
        session.add(
            EmployeeBrain(
                employee_id=charlie.id,
                personality="克制、爱查文档",
                goals="[]",
                interests="[]",
                learning_policy={"enabled": True},
                memory_policy={},
                curiosity=0.7,
            )
        )
        session.add(
            RuntimeInstance(
                employee_id=charlie.id,
                runtime_type=RuntimeType.mock.value,
                deployment_mode="mock",
                status="running",
            )
        )
        package = AccessPackage(
            slug="engineer", name="Engineer", description="", role="engineer", built_in=True
        )
        session.add(package)
        session.commit()
        ids = {
            "company": company.id,
            "departments": {d.slug: d.id for d in session.scalars(sa.select(Department))},
            "employees": {e.slug: e.id for e in session.scalars(sa.select(Employee))},
        }
    dept_by_slug = ids["departments"]
    emp_by_slug = ids["employees"]

    with engine.begin() as conn:
        # positions：两个部门都有 "Engineer" 同名头衔 → v12 的 code 冲突必须被消解而不是合并语义
        position_ids = {}
        for position_id, dept_slug, title in (
            (1, "engineering", "Software Engineer"),
            (2, "research", "Researcher"),
            (3, "engineering", "Engineer"),
        ):
            conn.execute(
                sa.text(
                    "INSERT INTO positions (id, department_id, title, level, created_at,"
                    " updated_at) VALUES (:id,:dept,:title,'1',:now,:now)"
                ),
                {
                    "id": position_id,
                    "dept": dept_by_slug[dept_slug],
                    "title": title,
                    "now": now,
                },
            )
            position_ids[title] = position_id

        # employments：
        #  #1 员工 charlie 生效主职（与 #4 冲突，较旧 ⇒ 必须被清洗掉）
        #  #2 dana 的**已关闭**历史行（窗口不能被任何步骤改写）
        #  #3 eve 的孤儿行（position_id=999 不存在 ⇒ 不许猜坑，也不许删行）
        #  #4 charlie 较新的生效主职（清洗后唯一保留）
        #  #5 dana 与 #2 同坑；#1/#2/#5 让 slot 轴也出现重复（position_id 相同）
        rows = [
            (1, "charlie", "engineering", 1, None, None, "active", {"kind": "hire"}),
            (
                2,
                "dana",
                "engineering",
                1,
                None,
                now - timedelta(days=5),
                "closed",
                {"kind": "transfer"},
            ),
            (3, "eve", "research", 999, None, None, "active", {"kind": "hire"}),
            (4, "charlie", "engineering", 3, None, None, "active", {"kind": "transfer"}),
            (5, "dana", "engineering", 1, None, None, "active", {"kind": "hire"}),
            (6, "frank", "research", None, None, None, "active", {"kind": "hire"}),
        ]
        for (
            row_id,
            slug,
            dept_slug,
            position_key,
            manager,
            close_at,
            status,
            meta,
        ) in rows:
            position_id = (
                position_ids.get(position_key, position_key)
                if isinstance(position_key, str)
                else position_key
            )
            conn.execute(
                sa.text(
                    "INSERT INTO employments (id, employee_id, department_id, position_id,"
                    " manager_employee_id, employment_status, joined_at, effective_from,"
                    " effective_to, metadata_json) VALUES (:id,:emp,:dept,:pos,:mgr,:status,"
                    " :joined,:from,:to,:meta)"
                ),
                {
                    "id": row_id,
                    "emp": emp_by_slug[slug],
                    "dept": dept_by_slug[dept_slug],
                    "pos": position_id,
                    "mgr": manager,
                    "status": status,
                    "joined": now - timedelta(days=30),
                    "from": now if close_at is None else now - timedelta(days=40),
                    "to": close_at,
                    "meta": json.dumps(meta),
                },
            )
    return now


def _snapshot(engine: Engine) -> dict:
    """迁移前锁定"绝不能变的东西"。"""
    with engine.connect() as conn:
        return {
            "employees": conn.execute(
                sa.text(
                    "SELECT id, slug, name, role, workspace_path, memory_namespace,"
                    " runtime_type, lifecycle_status FROM employees ORDER BY id"
                )
            ).all(),
            "brains": conn.execute(
                sa.text(
                    "SELECT employee_id, personality, curiosity, learning_policy"
                    " FROM employee_brains ORDER BY employee_id"
                )
            ).all(),
            "runtimes": conn.execute(
                sa.text(
                    "SELECT id, employee_id, runtime_type, deployment_mode, status"
                    " FROM runtime_instances ORDER BY id"
                )
            ).all(),
            "accounts": conn.execute(
                sa.text("SELECT id, employee_id, resource_type FROM resource_accounts ORDER BY id")
            ).all(),
            "employment_rows": conn.execute(
                sa.text(
                    "SELECT id, employee_id, department_id, position_id, employment_status,"
                    " joined_at, effective_from, effective_to FROM employments ORDER BY id"
                )
            ).all(),
        }


def test_upgrade_preserves_person_data_and_history(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, "upgrade", V11)
    _seed_legacy(engine)
    before = _snapshot(engine)
    assert len(before["employees"]) == 4
    assert len(before["employment_rows"]) == 6

    _run(engine, "upgrade", "head")

    after = _snapshot(engine)
    # A：人的一切不变 —— 职位可以变，人不能变
    assert after["employees"] == before["employees"]
    assert after["brains"] == before["brains"]
    assert after["runtimes"] == before["runtimes"]
    assert after["accounts"] == before["accounts"]
    # B：一行不删；**已关闭**的历史行时间窗原值保留
    assert [row[0] for row in after["employment_rows"]] == [
        row[0] for row in before["employment_rows"]
    ]
    closed_before = [row for row in before["employment_rows"] if row[0] == 2]
    closed_after = [row for row in after["employment_rows"] if row[0] == 2]
    assert closed_before == closed_after, "已关闭的任职历史被改写了"

    with engine.connect() as conn:
        rows = {
            row.id: row
            for row in conn.execute(
                sa.text(
                    "SELECT id, position_slot_id, assignment_type, is_primary,"
                    " position_title_snapshot, reason, metadata_json FROM employments ORDER BY id"
                )
            )
        }
        # C：回填结论
        assert {row.assignment_type for row in rows.values()} == {"primary"}
        assert all(bool(row.is_primary) for row in rows.values())
        assert rows[1].position_slot_id is not None
        assert rows[1].position_title_snapshot == "Software Engineer"
        assert rows[1].reason == "hire"
        assert rows[3].position_slot_id is None, "孤儿行不许被猜一个坑"
        orphan_meta = json.loads(rows[3].metadata_json)
        assert "normalized" not in orphan_meta  # 清洗与孤儿是两件事，别混标记

        # 无 position_id 的任职（dev 库的主流形态）：既不猜坑，也不被当成脏数据关掉。
        # 迁移后这个人就是 AVAILABLE —— 有身份、没编制，正是人才名册要暴露的状态。
        assert rows[6].position_slot_id is None
        assert "normalized" not in json.loads(rows[6].metadata_json)
        unslotted = conn.execute(
            sa.text(
                "SELECT COUNT(*) FROM employments WHERE effective_to IS NULL"
                " AND position_slot_id IS NULL"
            )
        ).scalar_one()
        assert unslotted == 2, (
            f"无坑的生效任职应当原样保留（孤儿 + 无 position_id），实际 {unslotted}"
        )
        all_rows = conn.execute(sa.text("SELECT COUNT(*) FROM employments")).scalar_one()
        assert all_rows == 6, "迁移不许丢行，也不许补行"

        # D：员工轴的冲突清洗保留最新一条
        active = (
            conn.execute(
                sa.text(
                    "SELECT id FROM employments WHERE employee_id = (SELECT id FROM employees"
                    " WHERE slug='charlie') AND effective_to IS NULL AND assignment_type='primary'"
                )
            )
            .scalars()
            .all()
        )
        assert list(active) == [4], f"清洗后应只剩最新主职 #4，实际 {list(active)}"
        cleaned_meta = json.loads(rows[1].metadata_json)
        assert cleaned_meta["normalized"]["by"] == "v14"
        assert cleaned_meta["normalized"]["kept_assignment_id"] == 4
        assert rows[1].assignment_type == "primary"
        status = conn.execute(
            sa.text("SELECT employment_status FROM employments WHERE id = 1")
        ).scalar_one()
        assert status == "superseded"

        # v12：同名不同部门的头衔没有被合并成同一个 code
        codes = (
            conn.execute(sa.text("SELECT code FROM position_definitions ORDER BY code"))
            .scalars()
            .all()
        )
        assert len(codes) == len(set(codes))
        assert "software_engineer" in codes and "engineer" in codes

        # 默认权限包从旧的 ROLE_TO_PACKAGE_SLUG 迁移过来后仍可解析
        linked = conn.execute(
            sa.text(
                "SELECT d.code, p.slug FROM position_definition_packages dp"
                " JOIN position_definitions d ON d.id = dp.position_definition_id"
                " JOIN access_packages p ON p.id = dp.package_id"
            )
        ).all()
        assert any(row.code == "engineer" and row.slug == "engineer" for row in linked), list(
            linked
        )

        # 占用态没有落库入口（ADR-2 的结构性表达）
        slot_columns = {c["name"] for c in sa.inspect(conn).get_columns("position_slots")}
        assert "administrative_status" in slot_columns
        assert "occupancy_status" not in slot_columns
    engine.dispose()


def test_partial_unique_index_blocks_second_active_primary(tmp_path):
    """同一员工再开一条生效主职必须被数据库拒绝；兼任/代理不受此约束（§7）。"""
    engine = _engine(tmp_path)
    _run(engine, "upgrade", V11)
    _seed_legacy(engine)
    _run(engine, "upgrade", "head")

    with engine.connect() as conn:
        eve = conn.execute(sa.text("SELECT id FROM employees WHERE slug='eve'")).scalar_one()
    with pytest.raises(sa.exc.IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO employments (employee_id, department_id, position_slot_id,"
                    " employment_status, joined_at, effective_from, metadata_json,"
                    " assignment_type, is_primary, reason, position_title_snapshot)"
                    " VALUES (:emp, 2, NULL, 'active', :now, :now, '{}', 'primary', 1, '', '')"
                ),
                {"emp": eve, "now": datetime.now(UTC)},
            )

    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO employments (employee_id, department_id, position_slot_id,"
                " employment_status, joined_at, effective_from, metadata_json,"
                " assignment_type, is_primary, reason, position_title_snapshot)"
                " VALUES (:emp, 2, NULL, 'active', :now, :now, '{}', 'secondary', 0, '', '')"
            ),
            {"emp": eve, "now": datetime.now(UTC)},
        )
    with engine.connect() as conn:
        assert (
            conn.execute(
                sa.text("SELECT COUNT(*) FROM employments WHERE assignment_type='secondary'")
            ).scalar_one()
            == 1
        )
    engine.dispose()


def test_upgrade_downgrade_reupgrade_is_lossless(tmp_path):
    engine = _engine(tmp_path)
    _run(engine, "upgrade", V11)
    _seed_legacy(engine)
    _run(engine, "upgrade", "head")
    with engine.connect() as conn:
        upgraded = conn.execute(
            sa.text("SELECT id, position_slot_id, employment_status FROM employments ORDER BY id")
        ).all()

    _run(engine, "downgrade", "-4")  # v14 → v13 → v12 → v11
    with engine.connect() as conn:
        columns = {c["name"] for c in sa.inspect(conn).get_columns("employments")}
        assert not (set(EMPLOYMENT_COLUMNS_ADDED) & columns)
        tables = set(sa.inspect(conn).get_table_names())
        assert "position_slots" not in tables and "position_definitions" not in tables
        ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM employments ORDER BY id"))]
        assert ids == [1, 2, 3, 4, 5, 6], "downgrade 删掉了历史行"

    _run(engine, "upgrade", "head")
    with engine.connect() as conn:
        again = conn.execute(
            sa.text("SELECT id, position_slot_id, employment_status FROM employments ORDER BY id")
        ).all()
        assert [row.id for row in again] == [row.id for row in upgraded]
        # 重跑后的回填结论与第一次一致（幂等 ⇒ 迁移可安全重放）
        assert {row.position_slot_id for row in again} == {row.position_slot_id for row in upgraded}
    engine.dispose()
