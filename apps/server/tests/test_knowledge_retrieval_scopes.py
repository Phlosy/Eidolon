"""K1 · 检索 scope 分层（docs/talent-ecosystem-plan.md §3 / vision §5）。

retrieval.retrieve_for_task 现在同时检索 private + department + company 三层；
命中排序：主键 token 重叠度降序，次键 scope 权重（company > department > private）。
公司隔离在 repo 层强制执行员工自己的 company_id（任务执行路径无 request identity）。
"""

import time
import uuid

from app.learning import retrieval
from app.models.enums import KnowledgeScope
from app.models.organization import Company, Department
from app.repositories import knowledge as knowledge_repo
from app.repositories import organization as org_repo


def _marker() -> str:
    return f"zzk1{int(time.time() * 1000)}{uuid.uuid4().hex[:6]}"


def _item(db, scope, marker, *, employee_id=None, department_id=None, topic_suffix=""):
    item = knowledge_repo.create_knowledge_item(
        db,
        scope=scope,
        owner_employee_id=employee_id,
        department_id=department_id,
        title=f"{marker}{topic_suffix} note",
        content="k1 scope layering test",
        topic=f"{marker}{topic_suffix}",
        status="active",
        confidence=0.9,
        sources=[],
    )
    db.commit()
    return item


def test_retrieval_mixes_all_three_scopes(db, employees_by_slug):
    alice = employees_by_slug["alice"]
    marker = _marker()
    _item(db, KnowledgeScope.private.value, marker, employee_id=alice["id"], topic_suffix="-p")
    _item(
        db,
        KnowledgeScope.department.value,
        marker,
        department_id=alice["department_id"],
        topic_suffix="-d",
    )
    _item(db, KnowledgeScope.company.value, marker, employee_id=alice["id"], topic_suffix="-c")

    result = retrieval.retrieve_for_task(db, alice["id"], f"{marker} task", "")
    assert f"{marker}-p" in result.knowledge
    assert f"{marker}-d" in result.knowledge
    assert f"{marker}-c" in result.knowledge


def test_scope_weight_breaks_overlap_ties(db, employees_by_slug):
    """重叠度相同时：company > department > private。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    # 三个条目重叠度同为 1（topic 各带一个不出现在任务里的后缀 token）
    _item(db, KnowledgeScope.private.value, marker, employee_id=alice["id"], topic_suffix="-p")
    _item(db, KnowledgeScope.company.value, marker, employee_id=alice["id"], topic_suffix="-c")
    _item(
        db,
        KnowledgeScope.department.value,
        marker,
        department_id=alice["department_id"],
        topic_suffix="-d",
    )

    knowledge = retrieval.retrieve_for_task(db, alice["id"], f"{marker} task", "").knowledge
    assert knowledge.index(f"{marker}-c") < knowledge.index(f"{marker}-d")
    assert knowledge.index(f"{marker}-d") < knowledge.index(f"{marker}-p")


def test_overlap_beats_scope_weight(db, employees_by_slug):
    """主键是重叠度：重叠更高的 private 命中排在重叠更低的 company 命中之前。"""
    alice = employees_by_slug["alice"]
    marker = _marker()
    # private 条目与任务共享 marker + alpha 两个 token（overlap=2）
    _item(db, KnowledgeScope.private.value, marker, employee_id=alice["id"], topic_suffix=" alpha")
    # company 条目只共享 marker（overlap=1）
    _item(db, KnowledgeScope.company.value, marker, employee_id=alice["id"], topic_suffix="-c")

    knowledge = retrieval.retrieve_for_task(db, alice["id"], f"{marker} alpha task", "").knowledge
    assert knowledge.index(f"{marker} alpha") < knowledge.index(f"{marker}-c")


def test_shared_scopes_do_not_leak_across_companies(db, employees_by_slug, default_company_id):
    """非请求上下文（identity=None）下，department/company 命中不得跨公司泄露。"""
    alice = employees_by_slug["alice"]
    marker = _marker()

    other = Company(
        name=f"K1Other {marker}", slug=f"k1-other-{uuid.uuid4().hex[:8]}", description=""
    )
    db.add(other)
    db.flush()
    other_dept = Department(
        company_id=other.id,
        name="Other Dept",
        slug=f"k1-dept-{uuid.uuid4().hex[:8]}",
        description="",
    )
    db.add(other_dept)
    db.flush()
    other_employee = org_repo.create_employee(
        db,
        company_id=other.id,
        department_id=other_dept.id,
        name="Other Eve",
        slug=f"k1-eve-{uuid.uuid4().hex[:8]}",
        role="engineer",
        workspace_path=f"/tmp/k1-ws-{uuid.uuid4().hex[:8]}",
        memory_namespace=f"k1-mem-{uuid.uuid4().hex[:8]}",
    )
    db.commit()

    _item(
        db, KnowledgeScope.company.value, marker, employee_id=other_employee.id, topic_suffix="-oc"
    )
    _item(
        db, KnowledgeScope.department.value, marker, department_id=other_dept.id, topic_suffix="-od"
    )

    # alice 检索不到别家公司的共享知识
    knowledge = retrieval.retrieve_for_task(db, alice["id"], f"{marker} task", "").knowledge
    assert f"{marker}-oc" not in knowledge
    assert f"{marker}-od" not in knowledge

    # 同公司员工可以检索到（过滤没把合法命中也杀掉）
    other_side = retrieval.retrieve_for_task(db, other_employee.id, f"{marker} task", "").knowledge
    assert f"{marker}-oc" in other_side
    assert f"{marker}-od" in other_side

    # 默认公司的 department 条目对别公司员工同样不可见
    _item(
        db,
        KnowledgeScope.department.value,
        marker,
        department_id=alice["department_id"],
        topic_suffix="-md",
    )
    other_side = retrieval.retrieve_for_task(db, other_employee.id, f"{marker} task", "").knowledge
    assert f"{marker}-md" not in other_side
