"""§3.3 的架构守卫：阈值只准住在 resolver + config 里。

这条约束一旦破口，`curiosity` 就会重新变成"散落在十几个文件里的 if"——正是本特性要解决的
问题本身。所以它必须是测试，而不是 code review 时的口头约定。
"""

import ast
import re
from pathlib import Path

import pytest

from app.brain import DEFAULT_POLICY, BrainTraits, policy_for, resolve
from app.brain.registry import TRAIT_REGISTRY
from app.learning import retrieval

APP_ROOT = Path(retrieval.__file__).resolve().parent.parent

# 允许直接接触 trait 的文件：契约层、写入侧（API / 持久化 / seed）、投影层。
TRAIT_AWARE_ALLOWLIST = {
    "brain/traits.py",
    "brain/resolver.py",
    "brain/registry.py",
    "brain/config.py",
    "brain/policy.py",
    "brain/projection.py",
    "brain/__init__.py",
    "services/lifecycle.py",  # hire：写侧
    "services/runtimes.py",  # PATCH /brain：写侧 + 摘要输出
    "services/seed.py",  # demo workforce 的默认人格
    "repositories/runtimes.py",  # ensure_brain：创建时补 traits
    "repositories/knowledge.py",  # SkillUsage 只透传 profile_revision
    "models/runtime.py",  # 列定义
    "schemas/runtime.py",  # 入参校验
}

CONSUMING_LAYERS = (
    "learning/",
    "workflow/",
    "runtimes/",
    "api/",
    "lifecycle/",
    "events/",
    "providers/",
)
TRAIT_NAMES = set(TRAIT_REGISTRY)


def _python_files(prefixes: tuple[str, ...]):
    for path in sorted(APP_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(APP_ROOT))
        if relative.startswith(prefixes):
            yield relative, path


def test_business_layers_never_read_individual_traits():
    """消费层不允许出现 `brain.curiosity` / `traits["curiosity"]` —— 只能读 BehaviorPolicy。"""
    violations: list[str] = []
    for relative, path in _python_files(CONSUMING_LAYERS):
        if relative in TRAIT_AWARE_ALLOWLIST:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in TRAIT_NAMES:
                violations.append(f"{relative}:{node.lineno} 读了 trait `{node.attr}`")
            if (
                isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and node.slice.value in TRAIT_NAMES
            ):
                violations.append(f"{relative}:{node.lineno} 按键取了 trait `{node.slice.value}`")
    assert not violations, (
        "行为代码直接读了人格字段，请改用 app.brain.policy_for() 的结果（§3.3）：\n"
        + "\n".join(violations)
    )


def test_no_numeric_comparison_next_to_a_trait_outside_the_resolver():
    """`if curiosity > 0.7` 这类散落判断是本特性要消灭的东西。"""
    threshold = re.compile(r"(?:<=|>=|<|>)\s*[\d.]+|[\d.]+\s*(?:<=|>=|<|>)")
    violations: list[str] = []
    for relative, path in _python_files(CONSUMING_LAYERS):
        if relative in TRAIT_AWARE_ALLOWLIST:
            continue
        text = path.read_text(encoding="utf-8")
        if not any(name in text for name in TRAIT_NAMES):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if any(name in stripped for name in TRAIT_NAMES) and threshold.search(stripped):
                violations.append(f"{relative}:{line_number}: {stripped}")
    assert not violations, "trait 附近出现阈值比较：\n" + "\n".join(violations)


def test_behavior_thresholds_are_not_hardcoded_in_business_layers():
    """行为阈值（候选技能线 / 延伸分上限）只能定义在 app/brain/config.py。"""
    suspicious_floats = {"0.7", "0.6", "0.65", "0.75", "0.8"}
    violations: list[str] = []
    for relative, path in _python_files(("learning/", "workflow/", "runtimes/", "api/")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        lines = text.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Constant) or not isinstance(node.value, float):
                continue
            rendered = f"{node.value:g}"
            if rendered not in suspicious_floats:
                continue
            line = lines[node.lineno - 1]
            # 只拦"看起来是行为阈值"的用法：与策略/候选/人格同现
            if re.search(r"candidate|curiosity|trait|behavior|policy", line, re.IGNORECASE):
                violations.append(f"{relative}:{node.lineno}: {line.strip()}")
    assert not violations, (
        "业务层硬编码了行为阈值，请放进 BehaviorPolicyConfig（§6）：\n" + "\n".join(violations)
    )


def test_resolve_is_not_called_outside_the_brain_package():
    """`resolve()` 属于 app.brain 内部；业务侧唯一入口是 policy_for()。"""
    violations: list[str] = []
    for relative, path in _python_files(("",)):
        if relative.startswith("brain/"):
            continue
        source = path.read_text(encoding="utf-8")
        imports_resolve = bool(re.search(r"from app\.brain[^\n]*import [^\n]*\bresolve\b", source))
        if imports_resolve:
            violations.append(relative)
    assert not violations, f"业务层直接引用 resolve()：{violations}"


def test_consuming_modules_go_through_policy_for():
    """派发与反思都必须经由 policy_for 消费策略（否则等于没有单一入口）。"""
    assert callable(policy_for) and callable(resolve)
    for module in ("workflow/orchestrator.py", "learning/reflection.py"):
        source = (APP_ROOT / module).read_text(encoding="utf-8")
        assert "policy_for" in source, f"{module} 没有通过 policy_for 消费策略"


def test_retrieval_has_no_private_quota_constant_in_its_body():
    """retrieval 里不能再自带额度常量：TOP_KNOWLEDGE 只作为"与旧实现等价"的锚点存在。"""
    source = (APP_ROOT / "learning/retrieval.py").read_text(encoding="utf-8")
    body = source[source.index("def retrieve_for_task") :]
    assert "TOP_KNOWLEDGE" not in body
    assert "retrieval.knowledge_limit" in body
    assert "policy" in body
    assert retrieval.TOP_KNOWLEDGE == DEFAULT_POLICY.retrieval.knowledge_limit  # 锚点仍然成立


def test_policy_is_immutable_and_carries_no_truth_semantics():
    from dataclasses import FrozenInstanceError

    with pytest.raises(FrozenInstanceError):
        DEFAULT_POLICY.retrieval.knowledge_limit = 99  # type: ignore[misc]
    snapshot = _policy_dict_keys()
    # 按 key 判断，而不是按子串：candidate_min_success_rate 是"准入门槛"，不是结果判定
    assert not snapshot & {"confidence", "success", "success_rate", "verdict", "accepted", "score"}
    assert set(snapshot) >= {
        "policy_version",
        "profile_revision",
        "band",
        "traits",
        "retrieval",
        "reflection",
        "learning",
    }


def _policy_dict_keys() -> set[str]:
    snapshot = DEFAULT_POLICY.as_dict()
    keys = set(snapshot)
    for section in ("retrieval", "reflection", "learning"):
        keys |= set(snapshot[section])
    return keys


def test_trait_registry_is_the_single_source_for_trait_names():
    """新 trait 只需要注册一次；消费侧的守卫靠这个集合自动扩展。"""
    assert "curiosity" in TRAIT_REGISTRY
    assert BrainTraits({}).snapshot().keys() == TRAIT_REGISTRY.keys()


# ---------------------------------------------------------------------------
# P4c：`employees.role` 是**镜像**，不是真相（ADR-5 / docs/workforce-domain-refactor.md）
#
# 为什么用 AST 而不是 grep：`employee.role`、`Employee.role`、`payload.role`、
# `DriveCollaborator.role`、`PackageSource.role` 在文本上长得一样。grep 要么把
# "权限包也有 role 字段"报成违规（然后有人把白名单越写越宽），要么放过
# `getattr(employee, "role")` 这种绕过。这里按语法树判定，并且**接收者不认识就当违规**
# ——逼新代码把话说清楚，而不是偷偷落进某个模糊类别。
#
# 为什么只扫 app/：
#   · migrations/ 必须能碰这一列（v12–v14 回填、P6 撤列都靠它），排除掉是有意的；
#   · tests/ 里"旧契约测试"读 `Employee.role` 是**断言旧行为**，不是新增业务读取；
#   · apps/web 读 API 返回的 `role` 字段属于兼容契约本身（值来自 position_compat），
#     不是第二个真相，所以不在本守卫范围内。
# ---------------------------------------------------------------------------

# 允许读旧列的模块 → 期望次数。数字变了（变大或变小）都会红：
# 变大 = 新代码开始依赖镜像；变小 = 有人清掉了历史包袱却没更新基线，清单会烂掉。
ROLE_READ_BASELINE: dict[str, int] = {
    "services/position_compat.py": 1,  # 唯一出口本身
    "services/lifecycle.py": 4,
    "services/seed.py": 2,
    "lifecycle/audit.py": 1,
    "runtimes/gateway.py": 1,
    "repositories/organization.py": 1,
}

# 每一处都要写清"谁在读、读来干什么、哪个阶段撤掉"。
# 没有理由、或者理由里没有撤除阶段的条目，本身就会红（见下面的元测试）。
ROLE_READ_REASONS: dict[str, str] = {
    "services/position_compat.py": (
        "唯一允许读镜像的模块（role_mirror 兜底分支）→ P6 撤列时连兜底一起删"
    ),
    "services/lifecycle.py": (
        "招聘事件载荷镜像 ×2 + resolve_packages(role=…) ×2 → P4d 换成人的 base 包 + 职位包"
    ),
    "services/seed.py": (
        "演示种子的 BRAIN_DEFAULTS 取键 + employee.created 兼容事件 → P6 撤列时改走职位定义"
    ),
    "lifecycle/audit.py": (
        "审计快照要记录**列原值**（取证要的是当时库里是什么，不是派生结论） → P6 撤列时随列消失"
    ),
    "runtimes/gateway.py": (
        "EmployeeRef 只把 role 当展示标签；改成派生值要在 gateway 注入 Session"
        " → P5 收敛 EmployeeRef"
    ),
    "repositories/organization.py": (
        "get_employee_by_role 的兜底查询本体，只被 position_compat 的回退分支调用 → P6 撤列时删除"
    ),
}

# 镜像**写入**点（构造员工时填 role）。撤列之前这些必须存在，但数量要锁住。
ROLE_WRITE_BASELINE: dict[str, int] = {
    "services/lifecycle.py": 1,
    "services/seed.py": 1,
}
ROLE_WRITE_REASONS: dict[str, str] = {
    "services/lifecycle.py": "招聘时写镜像列 → P6 撤列后由职位定义反查",
    "services/seed.py": "演示 workforce 建行时写镜像列 → P6 撤列后由职位定义给出身份",
}

# `.role` 属于别的模型的接收者（权限包 / 协作者 / 成员关系 / 入参）。
# 想加进来必须带上下文：这条清单每加一项，守卫就瞎一分。
NON_EMPLOYEE_ROLE_RECEIVERS = {
    "PackageSource",  # 权限包来源枚举自带 role 维度
    "auth",
    "membership",
    "collaborator",
    "c",  # DriveCollaborator 行
    "payload",  # 招聘入参
    "self",
    "node",
    "package",
    "item",
    "row",
    "user",
    "req",
    "connection",
    "grant",
}
EMPLOYEE_RECEIVER = re.compile(
    r"employee|^emp$|^emp_|^e$|person|founder|target|candidate|incumbent|holder|match|owner"
)


# app/ 下所有业务包都扫（新增目录自动覆盖，避免“忘了加前缀 = 自动放行”）
ALL_APP_PACKAGES = ("",)


def _receiver_name(node: ast.Attribute) -> str:
    """`a.b.c.role` → 最左边的名字，用来判断这个 `.role` 挂在谁身上。"""
    current: ast.expr = node.value
    while isinstance(current, ast.Attribute):
        current = current.value
    if isinstance(current, ast.Name):
        return current.id
    if isinstance(current, ast.Call):
        func = current.func
        return func.attr if isinstance(func, ast.Attribute) else ast.unparse(func)
    return ast.unparse(current)


def _is_employee_role_receiver(receiver: str) -> bool:
    if receiver == "Employee":  # 类上的列引用
        return True
    if receiver in NON_EMPLOYEE_ROLE_RECEIVERS:
        return False
    return bool(EMPLOYEE_RECEIVER.search(receiver))


def _role_sites() -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    """返回 (读取点, 写入点)：文件 → 行号列表。"""
    reads: dict[str, list[int]] = {}
    writes: dict[str, list[int]] = {}
    for relative, path in _python_files(ALL_APP_PACKAGES):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or node.attr != "role":
                continue
            if not _is_employee_role_receiver(_receiver_name(node)):
                continue
            bucket = writes if isinstance(node.ctx, ast.Store) else reads
            bucket.setdefault(relative, []).append(node.lineno)
    return reads, writes


def test_legacy_role_column_reads_stay_on_the_frozen_baseline():
    """新增一处 `employee.role` 读取 = 红。清掉一处但不更新基线也 = 红。"""
    reads, _ = _role_sites()
    unexpected = {f: lines for f, lines in reads.items() if f not in ROLE_READ_BASELINE}
    grew = {
        f: (len(lines), ROLE_READ_BASELINE[f])
        for f, lines in reads.items()
        if f in ROLE_READ_BASELINE and len(lines) > ROLE_READ_BASELINE[f]
    }
    shrank = {
        f: (len(reads.get(f, [])), ROLE_READ_BASELINE[f])
        for f in ROLE_READ_BASELINE
        if f not in unexpected and len(reads.get(f, [])) < ROLE_READ_BASELINE[f]
    }
    detail = []
    if unexpected:
        detail.append(
            "新模块开始读镜像列："
            + "; ".join(
                f"{f}:{','.join(map(str, lines))}" for f, lines in sorted(unexpected.items())
            )
        )
    if grew:
        detail.append(
            "既有模块多出读取点："
            + "; ".join(f"{f} {now}>{cap}" for f, (now, cap) in sorted(grew.items()))
        )
    if shrank:
        detail.append(
            "读取点变少却没更新 ROLE_READ_BASELINE/REASONS（清单不更新就会烂掉）："
            + "; ".join(f"{f} {now}<{cap}" for f, (now, cap) in sorted(shrank.items()))
        )
    assert not detail, (
        "employees.role 是镜像不是真相；业务代码请改用 position_compat 的\n"
        "legacy_role_of / role_mirror / employee_by_legacy_role：\n" + "\n".join(detail)
    )


def test_mirror_write_sites_are_the_hire_and_seed_paths_only():
    """`Employee(role=…)` 的写入点锁死在招聘与种子两处。"""
    _, attribute_writes = _role_sites()
    kwargs_writes: dict[str, list[int]] = {}
    for relative, path in _python_files(ALL_APP_PACKAGES):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.id if isinstance(callee, ast.Name) else getattr(callee, "attr", "")
            if name not in {"Employee", "create_employee"}:
                continue
            if any(kw.arg == "role" for kw in node.keywords):
                kwargs_writes.setdefault(relative, []).append(node.lineno)
    counted = {rel: len(lines) for rel, lines in kwargs_writes.items()}
    assert counted == ROLE_WRITE_BASELINE, (
        f"镜像写入点漂移：实际 {kwargs_writes}，基线 {ROLE_WRITE_BASELINE}"
    )
    assert not attribute_writes, f"不该有人直接给 employee.role 赋值：{attribute_writes}"


def test_get_employee_by_role_has_only_the_bridge_as_caller():
    """旧查找函数只准 `position_compat` 的回退分支调用。

    它按 `employees.role` 找人，会把"名册上 AVAILABLE、但镜像写着 ceo"的人当成 CEO 派活
    —— 所以新调用点一律红。要找人就问职位：`position_service` / `position_repo`。
    """
    offenders: list[str] = []
    for relative, path in _python_files(ALL_APP_PACKAGES):
        if relative == "services/position_compat.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "get_employee_by_role":
                offenders.append(f"{relative}:{node.lineno}")
    assert not offenders, (
        "只允许 position_compat 调 get_employee_by_role，其他模块请改走职位域：\n"
        + "\n".join(offenders)
    )


def test_no_dynamic_or_sql_bypasses_of_the_role_column():
    """`getattr(e, "role")`、`["role"]`、裸 SQL 里的 `employees.role` —— 全是绕道。"""
    offenders: list[str] = []
    for relative, path in _python_files(ALL_APP_PACKAGES):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and getattr(node.args[1], "value", None) == "role"
            ):
                offenders.append(f"{relative}:{node.lineno} getattr(…, 'role')")
            if isinstance(node, ast.Call) and getattr(
                node.func, "id", getattr(node.func, "attr", "")
            ) in {"text", "execute", "scalar", "scalars", "sql"}:
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        if "employees.role" in arg.value:
                            offenders.append(f"{relative}:{node.lineno} 裸 SQL 引用 employees.role")
            if (
                isinstance(node, ast.Compare)
                and isinstance(node.ops[0], (ast.Eq, ast.NotEq, ast.In, ast.NotIn))
                and isinstance(node.left, ast.Attribute)
                and node.left.attr == "role"
                and any(
                    isinstance(c, ast.Constant) and c.value in {m.value for m in _employee_roles()}
                    for c in node.comparators
                )
            ):
                offenders.append(
                    f"{relative}:{node.lineno} 拿 role 和字面量比：{ast.unparse(node)[:60]}"
                )
    assert not offenders, "旧列只能经 position_compat 读：\n" + "\n".join(offenders)


def _employee_roles() -> list:
    from app.models.enums import EmployeeRole

    return list(EmployeeRole)


def test_every_grandfathered_site_has_a_reason_naming_its_removal_phase():
    """grandfather 清单必须可审计：文件对齐 + 有理由 + 理由里写着谁撤它。

    "先记一笔以后再说"是本领域最容易长出来的第二真相。
    """
    assert set(ROLE_READ_BASELINE) == set(ROLE_READ_REASONS), "基线与理由清单不一致"
    assert set(ROLE_WRITE_BASELINE) == set(ROLE_WRITE_REASONS), "写入基线与理由不一致"
    for reason in [*ROLE_READ_REASONS.values(), *ROLE_WRITE_REASONS.values()]:
        assert re.search(r"P4d|P5|P6|撤列|随列消失|删除", reason), f"理由没写撤除阶段：{reason}"
    # compat 自己那一处永远是 1，且它是唯一一个"没有撤除阶段"的允许项
    assert ROLE_READ_BASELINE["services/position_compat.py"] == 1


def test_the_bridge_still_exposes_all_three_doors():
    """正门关了，人就会翻窗。三个出口必须一直在，业务才不必回头读旧列。"""
    from app.services import position_compat

    for name in ("legacy_role_of", "role_mirror", "employee_by_legacy_role", "enrich_employee"):
        assert hasattr(position_compat, name), f"position_compat.{name} 不见了"
    # 单个取值必须委托批量版，否则两份取值顺序会各自漂移
    import inspect

    body = inspect.getsource(position_compat.legacy_role_of)
    assert "role_mirror" in body and "employee_current_position" not in body


def test_api_role_field_is_still_derived_not_stored():
    """员工接口里的 `role` 走 enrich_employee（派生），不许有人改成直接回 ORM 列。"""
    reads, _ = _role_sites()
    offenders = {rel: lines for rel, lines in reads.items() if rel.startswith("api/")}
    assert not offenders, f"API 层直接读镜像列，请改走 position_compat：{offenders}"
    from app.services import position_compat

    text = position_compat.enrich_employee.__doc__ or ""
    assert "派生" in text or "derive" in text.lower(), "enrich_employee 要说清楚自己是派生出口"


# ---------------------------------------------------------------------------
# 上一条目的元测试：这些守卫必须"会红"。
#
# 只断言"当前没有违规"是不够的 —— 扫描逻辑写坏（目录没扫到、接收者判定失效、
# 白名单被整体短路）时结果同样是 0，测试照样绿。这里用合成源码真的跑一遍判定函数，
# 并证明基线本身非空（P6 撤列那天，空基线会让上面那组断言全部静默失效）。
# ---------------------------------------------------------------------------


def _role_reads_of(source: str) -> dict[str, list[int]]:
    tree = ast.parse(source)
    found: dict[str, list[int]] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "role"
            and not isinstance(node.ctx, ast.Store)
            and _is_employee_role_receiver(_receiver_name(node))
        ):
            found.setdefault("synthetic.py", []).append(node.lineno)
    return found


def test_the_role_scan_actually_detects_reads():
    """判定函数必须认得真违规，并且别把别人的 role 字段当成员工列。"""
    assert _role_reads_of("def f(employee):\n    return employee.role == 'ceo'\n"), (
        "最典型的 `employee.role` 读取都没抓到 —— 扫描已经失效"
    )
    assert _role_reads_of("def f(db):\n    return db.scalars(select(Employee.role))\n"), (
        "类级列引用 Employee.role 没抓到"
    )
    assert _role_reads_of("def f(emp):\n    return emp.role\n"), "简写接收者没抓到"
    # 已知盲区：`row` 这种中性名字在非员工清单里（它确实常指协作者/成员关系行）。
    # 清单每加一项守卫就瞎一分 —— 把盲区写出来盯着，而不是假装判定是完美的。
    assert not _role_reads_of("def f(row):\n    return row.role\n")
    # 非员工接收者（不同模型的 role 维度）不该被算进来
    assert not _role_reads_of("def f(payload):\n    return payload.role\n")
    assert not _role_reads_of("def f(pkg):\n    return PackageSource.role\n")
    assert not _role_reads_of("def f(m):\n    return m.membership.role\n")
    # 写入侧不算读取
    assert not _role_reads_of("def f(employee):\n    employee.role = 'ceo'\n")


def test_the_get_employee_by_role_scan_actually_detects_calls():
    """调用点扫描要真的能认出 `org_repo.get_employee_by_role(...)`。"""
    tree = ast.parse(
        "def f(db, company_id):\n    return org_repo.get_employee_by_role(db, company_id, 'ceo')\n"
    )
    hits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "get_employee_by_role"
    ]
    assert len(hits) == 1, "旧查找的调用点扫描失效"


def test_grandfather_baselines_are_not_empty():
    """基线为空 = P6 撤列后这组守卫集体静默失效，那时必须有人显式删掉它们。"""
    assert sum(ROLE_READ_BASELINE.values()) >= 5, (
        "读取基线缩到接近 0：镜像列快没了，请连同本节的守卫一起清掉，不要留下永远为真的空断言"
    )
    assert ROLE_WRITE_BASELINE, "写入基线为空 —— 说明写入侧扫描已失效或该删除"
