"""v12.position_definitions

把"职位"从 `Employee.role` 里剥出来的第一步（docs/position-system.md §2.1、
docs/workforce-domain-refactor.md §6）。

本迁移**只做加法**：建 `position_definitions` + `position_definition_packages`，
并把 v0.4 遗留的 `positions`（部门内自由文本 title/level）收编成真正的职位模板。
旧 `positions` 表此时仍被 `services/lifecycle.py` / `services/seed.py` 读取，
所以它继续存在，直到 v18 无人引用才删除 —— 中间任何一刻都不存在"两套职位真相"，
因为读路径还没切。

`code` 在公司内唯一：种子数据里不同部门可能出现同名职位（例如两个部门都叫
"Engineer"），因此冲突时回退成 `{department}-{title}`，保证确定性且不静默合并语义不同的定义。
两条拍板约束写在这里：
  * `template_scope` 只用 `company`（迁移产物都是公司行）；内置 `system` 模板由 seed 在
    P4 落地，因为那时才有消费方（分配向导 / 空缺列表）。
  * `legacy_role` 只为兼容镜像服务，新业务禁止读取（ADR-5 的守卫测试负责）。

Verify: `upgrade head` → `downgrade -2` → `upgrade head` 幂等；`alembic check` 无漂移。
"""

from __future__ import annotations

import re

import json

import sqlalchemy as sa
from alembic import op

from app.models.base import utcnow

revision = "k6f9b2c5d841"
down_revision = "j5e8a1b4c730"
branch_labels = None
depends_on = None

# 与 app/lifecycle/access.py 的两张映射表逐字对齐；改那边必须同时改这里（有测试守着）。
DEPARTMENT_TO_ROLE = {
    "executive": "ceo",
    "product": "product_manager",
    "research": "researcher",
    "engineering": "engineer",
    "qa": "qa_engineer",
}
ROLE_TO_PACKAGE_SLUG = {
    "ceo": "ceo",
    "product_manager": "product-manager",
    "researcher": "researcher",
    "engineer": "engineer",
    "qa_engineer": "qa-engineer",
}
ROLE_TO_FAMILY = {
    "ceo": "management",
    "product_manager": "product",
    "researcher": "research",
    "engineer": "engineering",
    "qa_engineer": "qa",
}

definitions = sa.table(
    "position_definitions",
    sa.column("id"),
    sa.column("company_id"),
    sa.column("template_scope"),
    sa.column("code"),
    sa.column("name"),
    sa.column("description"),
    sa.column("job_family"),
    sa.column("level"),
    sa.column("responsibilities"),
    sa.column("career_path_metadata"),
    sa.column("built_in"),
    sa.column("legacy_role"),
    sa.column("created_at"),
    sa.column("updated_at"),
)
definition_packages = sa.table(
    "position_definition_packages",
    sa.column("id"),
    sa.column("position_definition_id"),
    sa.column("package_id"),
    sa.column("created_at"),
)


def _slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or "position"


def _level_of(raw: str | None) -> int:
    """旧 `positions.level` 是自由文本；只有能解析成序数的才用，其余保守取 1。"""
    try:
        return max(1, int(str(raw).strip()))
    except (TypeError, ValueError):
        return 1


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "position_definitions" not in tables:
        op.create_table(
            "position_definitions",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("company_id", sa.Integer(), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("template_scope", sa.String(length=20), nullable=False),
            sa.Column("code", sa.String(length=100), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("description", sa.String(length=2000), nullable=False),
            sa.Column("job_family", sa.String(length=50), nullable=False),
            sa.Column("level", sa.Integer(), nullable=False),
            sa.Column("responsibilities", sa.JSON(), nullable=False),
            sa.Column("career_path_metadata", sa.JSON(), nullable=False),
            sa.Column("built_in", sa.Boolean(), nullable=False),
            sa.Column("legacy_role", sa.String(length=50), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("company_id", "code", name="uq_position_definition_code"),
        )
        # 刻意不另建 (company_id) / (code) 索引：uq_position_definition_code 已经建了
        # 以 company_id 为前缀的索引，再加两个只是给写路径添负担（也避免 autogen 漂移）。

    if "position_definition_packages" not in tables:
        op.create_table(
            "position_definition_packages",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "position_definition_id",
                sa.Integer(),
                sa.ForeignKey("position_definitions.id"),
                nullable=False,
            ),
            sa.Column(
                "package_id", sa.Integer(), sa.ForeignKey("access_packages.id"), nullable=False
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.UniqueConstraint("position_definition_id", "package_id", name="uq_position_package"),
        )
        op.create_index(
            "ix_position_definition_packages_position_definition_id",
            "position_definition_packages",
            ["position_definition_id"],
        )
        op.create_index(
            "ix_position_definition_packages_package_id",
            "position_definition_packages",
            ["package_id"],
        )

    _backfill(bind)


def _as_dict(raw: object) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _backfill(bind) -> None:
    """`positions(department, title, level)` → 职位模板 + 其默认权限包。

    幂等靠 **签名** `(company, __dept_id, name)` 识别，不靠 code：code 会被下面的
    冲突改名，用 code 判重会在重跑时把同一个职位建成两条、或者干脆漏建。
    """
    if "positions" not in set(sa.inspect(bind).get_table_names()):
        return
    now = utcnow()
    taken_codes: set[tuple[int | None, str]] = set()
    by_signature: dict[tuple[int | None, int | None, str], int] = {}
    for row in bind.execute(
        sa.text(
            "SELECT id, company_id, code, name, career_path_metadata FROM position_definitions"
            " WHERE template_scope = 'company'"
        )
    ):
        taken_codes.add((row.company_id, row.code))
        marker = _as_dict(row.career_path_metadata)
        if marker.get("__backfill") == "v12":
            by_signature[(row.company_id, marker.get("__dept_id"), row.name)] = row.id

    source = bind.execute(
        sa.text(
            "SELECT p.id AS position_id, dd.company_id AS company_id, dd.id AS department_id, "
            "dd.slug AS dept_slug, p.title AS title, p.level AS level "
            "FROM positions p JOIN departments dd ON dd.id = p.department_id "
            "ORDER BY p.id"
        )
    )
    package_ids = {
        row.slug: row.id for row in bind.execute(sa.text("SELECT id, slug FROM access_packages"))
    }

    for row in source:
        signature = (row.company_id, row.department_id, row.title)
        if signature in by_signature:
            continue  # 重跑：复用上一次的定义（v13 靠 __dept_id 标记还原映射）
        legacy_role = DEPARTMENT_TO_ROLE.get(row.dept_slug)
        # 同名不同部门的头衔不能合并成同一个 code（语义不同）；冲突就加部门前缀，
        # 再冲突就加数字后缀 —— 绝不静默跳过，跳过等于这个职位既没定义也没坑。
        code = _slugify(row.title)
        attempt = 1
        while (row.company_id, code) in taken_codes:
            attempt += 1
            code = (
                _slugify(f"{row.dept_slug}-{row.title}")
                if attempt == 2
                else _slugify(f"{row.dept_slug}-{row.title}-{attempt}")
            )
        taken_codes.add((row.company_id, code))
        responsibilities = [{"title": row.title}] if row.title else []
        result = bind.execute(
            definitions.insert().values(
                company_id=row.company_id,
                template_scope="company",
                code=code,
                name=row.title,
                description="",
                job_family=ROLE_TO_FAMILY.get(legacy_role or "", row.dept_slug or ""),
                level=_level_of(row.level),
                responsibilities=json.dumps(responsibilities),
                # __dept_id 只用于本次回填的可追溯与幂等，不参与业务读取。
                career_path_metadata=json.dumps(
                    {"__backfill": "v12", "__dept_id": row.department_id}
                ),
                built_in=True,
                legacy_role=legacy_role,
                created_at=now,
                updated_at=now,
            )
        )
        # 用 (company_id, code) 回读 id：`definitions` 是通用 sa.table() 构造，
        # SQLAlchemy 不知道主键，`result.inserted_primary_key` 会是空元组。
        # code 在 (company_id, code) 唯一约束下本来就是确定的，回读比依赖驱动行为稳妥。
        result.close()
        definition_id = int(
            bind.execute(
                sa.text(
                    "SELECT id FROM position_definitions WHERE company_id = :c AND code = :code"
                ),
                {"c": row.company_id, "code": code},
            ).scalar_one()
        )
        by_signature[signature] = definition_id

        package_slug = ROLE_TO_PACKAGE_SLUG.get(legacy_role or "")
        package_id = package_ids.get(package_slug or "")
        if package_id is not None:
            bind.execute(
                definition_packages.insert().values(
                    position_definition_id=definition_id,
                    package_id=package_id,
                    created_at=now,
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "position_definition_packages" in tables:
        op.drop_index(
            "ix_position_definition_packages_package_id",
            table_name="position_definition_packages",
        )
        op.drop_index(
            "ix_position_definition_packages_position_definition_id",
            table_name="position_definition_packages",
        )
        op.drop_table("position_definition_packages")
    if "position_definitions" in tables:
        op.drop_table("position_definitions")
