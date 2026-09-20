"""M2.6 **Artifact Handoff**（设计 §14c，W19 / H1–H8）。

目标一句话：**让 Agent A 的输出真正成为 Agent B 的输入**，且这件事可追溯。

```text
Manager 声明：B 要用 A 的产品        ← task_inputs（指向 **Task**，不是 artifact）
A 跑完 → 产物落 Drive               ← drive_nodes.task_id = A（产出归属，H2）
B 就绪 → 系统**在运行期解析**输入     ← resolve_input_artifacts（H4）
B 开工 → 记下"这些产物被 B 在哪次会话用掉了" ← artifact_links（H3/G5）
```

四条边界：

1. **不建第二套 Artifact 系统**（H1）：内容/版本/sha256 都在 Drive。
2. **声明 ≠ 引用**（H4）：声明的硬约束是**顺序保证**（上游必须是 DAG 祖先，H5），
   产物由系统在运行期解析 —— 因为声明时产物还不存在。
3. **只消费已完成的产出**（H8）：在跑的 Task 的产物永不被引用（服务层拒绝）。
4. **同一个事实不留两个落点**（H3）：产出归属只有 `drive_nodes.task_id` 一处；
   链接表只记**使用**。

这个模块是**唯一的交接口径**：HTTP 读面、Agent 工具面、执行面（TaskContext）
全部经它，不各自写一遍查询（T2/T11 同款纪律）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.core.transaction_effects import ensure_sqlite_transaction
from app.models.drive import DriveRevision
from app.models.enums import ArtifactLinkRole, DriveNodeKind, DriveZone, TaskStatus
from app.models.handoff import ArtifactLink, TaskInput
from app.models.project import Task, WorkSession
from app.repositories import drive as drive_repo
from app.repositories import handoff as handoff_repo
from app.repositories import project as project_repo
from app.work import artifact_versions
from app.work import contracts as C
from app.work.artifact_versions import LineageViolation

# ---------------------------------------------------------------------------
# 读模型（HTTP 读面 / Agent 读工具 / 执行面共用；G2/G5/H7）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArtifactRef:
    """一个产物的**引用 + 归属**（内容按需读，不在这里塞全文）。"""

    artifact_id: int
    title: str
    doc_type: str
    task_id: int | None
    task_title: str | None
    work_session_id: int | None
    version: int
    sha256: str
    revision_id: int | None = None

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "title": self.title,
            "doc_type": self.doc_type,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "work_session_id": self.work_session_id,
            "version": self.version,
            "sha256": self.sha256,
            "revision_id": self.revision_id,
        }


@dataclass(frozen=True)
class InputArtifactRef(ArtifactRef):
    """交给下游的输入（H6）：比 `ArtifactRef` 多一段**有界**内容摘要。"""

    source_task_id: int = 0
    source_task_title: str = ""
    excerpt: str = ""

    def as_dict(self) -> dict:
        return {
            **super().as_dict(),
            "source_task_id": self.source_task_id,
            "source_task_title": self.source_task_title,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class ConsumedRef:
    """一次**使用**事实：谁在哪次会话用掉了这个产物（H3/G5）。"""

    artifact_id: int
    title: str
    doc_type: str
    task_id: int
    task_title: str | None
    work_session_id: int | None
    actor_employee_id: int | None
    reason: str
    created_at: str
    revision_id: int | None = None
    version: int | None = None
    sha256: str | None = None

    def as_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "title": self.title,
            "doc_type": self.doc_type,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "work_session_id": self.work_session_id,
            "actor_employee_id": self.actor_employee_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "revision_id": self.revision_id,
            "version": self.version,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class DeclaredInput:
    """一条输入声明 + 它的**当前状态**（事实，不做判断）。"""

    source_task_id: int
    source_task_title: str
    source_task_status: str
    artifact_count: int

    @property
    def ready(self) -> bool:
        return self.source_task_status == TaskStatus.done.value and self.artifact_count > 0

    def as_dict(self) -> dict:
        return {
            "source_task_id": self.source_task_id,
            "source_task_title": self.source_task_title,
            "source_task_status": self.source_task_status,
            "artifact_count": self.artifact_count,
            "ready": self.ready,
        }


@dataclass(frozen=True)
class LineageHop:
    """上游链上的一跳（G2）：`depth` = 离查询目标的距离。"""

    depth: int
    task_id: int
    task_title: str
    task_status: str
    artifact_id: int
    artifact_title: str
    doc_type: str

    def as_dict(self) -> dict:
        return {
            "depth": self.depth,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "task_status": self.task_status,
            "artifact_id": self.artifact_id,
            "artifact_title": self.artifact_title,
            "doc_type": self.doc_type,
        }


@dataclass(frozen=True)
class TaskArtifactReport:
    """`GET /tasks/{id}/artifacts` 的全部事实（produced + consumed + declared + lineage）。"""

    task_id: int
    produced: tuple[ArtifactRef, ...] = ()
    consumed: tuple[ConsumedRef, ...] = ()
    declared_inputs: tuple[DeclaredInput, ...] = ()
    inputs: tuple[InputArtifactRef, ...] = ()
    upstream: tuple[LineageHop, ...] = ()
    missing_input_sources: tuple[int, ...] = ()
    produces: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "produces": list(self.produces),
            "produced": [item.as_dict() for item in self.produced],
            "consumed": [item.as_dict() for item in self.consumed],
            "declared_inputs": [item.as_dict() for item in self.declared_inputs],
            "inputs": [item.as_dict() for item in self.inputs],
            "upstream": [item.as_dict() for item in self.upstream],
            "missing_input_sources": list(self.missing_input_sources),
        }


# ---------------------------------------------------------------------------
# 产出归属（H2）
# ---------------------------------------------------------------------------


def artifact_ref(db: Session, node) -> ArtifactRef:
    task = project_repo.get_task(db, int(node.task_id)) if node.task_id else None
    pinned = artifact_versions.pinned_revision(db, node)
    revision = pinned or drive_repo.get_revision(db, int(node.id), int(node.current_version))
    return ArtifactRef(
        artifact_id=int(node.id),
        title=node.name,
        doc_type=node.doc_type or "",
        task_id=int(node.task_id) if node.task_id else None,
        task_title=task.title if task else None,
        work_session_id=int(node.work_session_id) if node.work_session_id else None,
        version=int(revision.version if pinned else node.current_version),
        sha256=(revision.sha256 if revision else ""),
        revision_id=int(pinned.id) if pinned else None,
    )


def produced_artifacts(db: Session, task_id: int) -> tuple[ArtifactRef, ...]:
    """这个 Task 产出了什么（产出归属 = `drive_nodes.task_id`）。"""
    return tuple(artifact_ref(db, node) for node in artifact_versions.current_nodes(db, task_id))


# ---------------------------------------------------------------------------
# 输入声明（H4/H5）
# ---------------------------------------------------------------------------


def ancestor_task_ids(db: Session, task: Task) -> set[int]:
    """DAG 祖先闭包（沿 `depends_on` 反向走）。"""
    deps: dict[int, list[int]] = {}
    for row in project_repo.list_dependencies(db, int(task.project_id)):
        deps.setdefault(int(row.task_id), []).append(int(row.depends_on_id))
    seen: set[int] = set()
    stack = list(deps.get(int(task.id), ()))
    while stack:
        node = stack.pop()
        if node in seen:
            continue
        seen.add(node)
        stack.extend(deps.get(node, ()))
    return seen


def validate_input_sources(db: Session, task: Task, source_task_ids: list[int]) -> None:
    """声明校验（H5）：同项目、不自消费、**必须是 DAG 祖先**（顺序保证）。

    为什么必须是祖先：声明的是"我要用它的产品"，但产物只有上游跑完才存在。
    如果它不一定是先跑的，这条声明就不是一条可执行计划 —— 系统拒绝，
    而不是"到时候再说"。
    """
    ancestors = ancestor_task_ids(db, task)
    for source_id in source_task_ids:
        source = project_repo.get_task(db, int(source_id))
        if source is None:
            raise LineageViolation(f"input source task {source_id} not found")
        if int(source.project_id) != int(task.project_id):
            raise LineageViolation(f"input source task {source_id} is in another project")
        if int(source.id) == int(task.id):
            raise LineageViolation("a task never consumes its own output")
        if int(source.id) not in ancestors:
            raise LineageViolation(
                f"input source task {source_id} is not a DAG ancestor of task {task.id} "
                "(add the dependency first: 输入必须有顺序保证)"
            )


def declare_inputs(
    db: Session,
    *,
    task: Task,
    source_task_ids: list[int],
    commit: bool = True,
) -> list[TaskInput]:
    """写入输入声明（幂等：已存在的声明不重复插入）。"""
    unique = list(dict.fromkeys(int(item) for item in source_task_ids))
    if not unique:
        return []
    validate_input_sources(db, task, unique)
    existing = {int(row.source_task_id) for row in handoff_repo.list_inputs(db, int(task.id))}
    rows = [
        handoff_repo.add_input(db, task_id=int(task.id), source_task_id=source_id)
        for source_id in unique
        if source_id not in existing
    ]
    if commit:
        db.commit()
    return rows


def declared_inputs(db: Session, task_id: int) -> tuple[DeclaredInput, ...]:
    """声明清单 + 每条声明**当前**的事实状态（上游什么状态、产出了几个产物）。"""
    out: list[DeclaredInput] = []
    for row in handoff_repo.list_inputs(db, int(task_id)):
        source_id = int(row.source_task_id)
        source = project_repo.get_task(db, source_id)
        out.append(
            DeclaredInput(
                source_task_id=source_id,
                source_task_title=source.title if source else "(已删除)",
                source_task_status=source.status if source else "",
                artifact_count=(
                    len(artifact_versions.current_nodes(db, source_id))
                    if source
                    and (
                        source.status != TaskStatus.done.value
                        or artifact_versions.formally_approved(db, source)
                    )
                    else 0
                ),
            )
        )
    return tuple(out)


def missing_input_sources(db: Session, task: Task) -> tuple[int, ...]:
    """声明的上游**跑完了却没产出**的（⇒ 计划与事实不符，交管理层，H5/R10）。

    注意只针对 `done`：还没跑的上游由 DAG 保证（祖先），不算"缺失"。
    """
    missing: list[int] = []
    for item in declared_inputs(db, int(task.id)):
        if item.source_task_status == TaskStatus.done.value and item.artifact_count == 0:
            missing.append(item.source_task_id)
    return tuple(missing)


# ---------------------------------------------------------------------------
# 运行期解析（H4/H6）
# ---------------------------------------------------------------------------


def _excerpt(db: Session, node, limit: int) -> str:
    content = artifact_versions.read_content(db, node)
    if len(content) <= limit:
        return content
    return content[:limit] + "\n…（截断，仅展示本次版本的部分内容）"


def resolve_input_artifacts(
    db: Session, task: Task, *, excerpt_chars: int = C.INPUT_ARTIFACT_EXCERPT_CHARS
) -> tuple[InputArtifactRef, ...]:
    """运行期解析这个 Task 的输入（H4/H6）。

    来源两处，按优先级去重：

    1. **声明**的上游 Task（`task_inputs`）→ 取其产出归属为它的产物；
    2. **显式消费**的产物（`artifact_links`，`role=consumed_by`）。

    **只取已完成产出**（H8）：正在跑的 Task 的产物不进上下文。
    """
    resolved: dict[int, InputArtifactRef] = {}
    for item in declared_inputs(db, int(task.id)):
        if not item.ready:
            continue
        for node in artifact_versions.current_nodes(db, item.source_task_id):
            _validate_consumer(db, task, node)
            ref = artifact_ref(db, node)
            resolved[int(node.id)] = InputArtifactRef(
                **{
                    **ref.__dict__,
                    "source_task_id": item.source_task_id,
                    "source_task_title": item.source_task_title,
                    "excerpt": _excerpt(db, node, excerpt_chars),
                }
            )
    for link in handoff_repo.list_artifact_links(db, task_id=int(task.id)):
        node = drive_repo.get_node(db, int(link.artifact_id))
        if node is None or int(node.id) in resolved:
            continue
        producer = project_repo.get_task(db, int(node.task_id)) if node.task_id else None
        if producer is not None and str(producer.status) != TaskStatus.done.value:
            # H8：在跑的产物永不被引用（哪怕链接已经存在）
            continue
        if producer is not None and (
            not artifact_versions.formally_approved(db, producer)
            or int(node.id) not in {n.id for n in artifact_versions.current_nodes(db, producer.id)}
        ):
            continue
        _validate_consumer(db, task, node)
        ref = artifact_ref(db, node)
        resolved[int(node.id)] = InputArtifactRef(
            **{
                **ref.__dict__,
                "source_task_id": int(node.task_id) if node.task_id else 0,
                "source_task_title": producer.title if producer else "",
                "excerpt": _excerpt(db, node, excerpt_chars),
            }
        )
    return tuple(resolved.values())


# ---------------------------------------------------------------------------
# 使用事实（H3/G5）
# ---------------------------------------------------------------------------


def record_consumed(
    db: Session,
    *,
    task_id: int,
    artifact_ids: list[int],
    artifact_refs: tuple[InputArtifactRef, ...] | list[InputArtifactRef] | None = None,
    work_session_id: int | None = None,
    actor_employee_id: int | None = None,
    reason: str = "auto: declared input",
    commit: bool = True,
) -> list[ArtifactLink]:
    """Append actual per-session use of the exact input refs handed to the worker."""
    ensure_sqlite_transaction(db)
    db.flush()
    task = db.get(Task, int(task_id), populate_existing=True)
    work = (
        db.get(WorkSession, work_session_id, populate_existing=True)
        if work_session_id is not None
        else None
    )
    if task is None or (
        work_session_id is not None
        and (
            work is None
            or work.task_id != task.id
            or work.purpose != "worker"
            or work.status != "running"
            or task.status != TaskStatus.in_progress.value
            or (actor_employee_id is not None and actor_employee_id != work.employee_id)
        )
    ):
        raise LineageViolation("artifact use does not match its active worker session")
    if work is not None:
        actor_employee_id = work.employee_id
    ids = list(dict.fromkeys(int(i) for i in artifact_ids))
    supplied = {ref.artifact_id: ref for ref in artifact_refs or ()}
    if artifact_refs is not None and (
        len(supplied) != len(artifact_refs) or set(supplied) != set(ids)
    ):
        raise LineageViolation("artifact input context does not match consumed references")
    selected = {}
    for artifact_id in ids:
        node = drive_repo.get_node(db, artifact_id)
        _validate_consumer(db, task, node)
        ref = artifact_ref(db, node)
        if artifact_refs is not None:
            expected = InputArtifactRef(
                **ref.__dict__,
                source_task_id=ref.task_id or 0,
                source_task_title=ref.task_title or "",
                excerpt=_excerpt(db, node, C.INPUT_ARTIFACT_EXCERPT_CHARS),
            )
            if supplied[artifact_id] != expected:
                raise LineageViolation("artifact input context changed before recording use")
        selected[artifact_id] = ref.revision_id
    existing = {
        int(link.artifact_id): link
        for link in handoff_repo.list_artifact_links(db, task_id=int(task_id))
        if link.role == ArtifactLinkRole.consumed_by.value
        and link.work_session_id == work_session_id
    }
    for artifact_id, revision_id in selected.items():
        if artifact_id in existing and existing[artifact_id].revision_id != revision_id:
            raise LineageViolation("original artifact use has a different or unknown revision")
    rows = [
        handoff_repo.add_artifact_link(
            db,
            artifact_id=int(artifact_id),
            task_id=int(task_id),
            role=ArtifactLinkRole.consumed_by.value,
            work_session_id=work_session_id,
            actor_employee_id=actor_employee_id,
            reason=reason,
            revision_id=selected[artifact_id],
        )
        for artifact_id in ids
        if int(artifact_id) not in existing
    ]
    if commit:
        db.commit()
    return rows


def _validate_consumer(db: Session, task: Task, node) -> None:
    if node is None or node.kind != DriveNodeKind.document.value:
        raise LineageViolation("artifact not found")
    project = project_repo.get_project(db, task.project_id)
    if (node.project_id is not None and node.project_id != task.project_id) or (
        node.company_id is not None and (project is None or node.company_id != project.company_id)
    ):
        raise LineageViolation("artifact belongs to another project or company")
    if node.task_id == task.id:
        raise LineageViolation("a task may not consume its own artifact")
    artifact_versions.require_consumable(db, node)


def consume_artifact(
    db: Session,
    *,
    task: Task,
    artifact_id: int,
    actor_employee_id: int | None = None,
    reason: str = "explicit",
    commit: bool = True,
) -> ArtifactLink:
    """显式消费一个产物（**H8**：由未完成的 Task 产出的产物一律拒绝）。

    这是"跨分支交接"的入口：Manager 可以说"B 也要用 C 的测试报告"，
    但 C 必须已经跑完 —— 系统只消费**已完成**的产出。
    """
    ensure_sqlite_transaction(db)
    db.flush()
    node = drive_repo.get_node(db, int(artifact_id))
    _validate_consumer(db, task, node)

    existing = next(
        (
            link
            for link in handoff_repo.list_artifact_links(db, task_id=int(task.id))
            if int(link.artifact_id) == int(artifact_id)
        ),
        None,
    )
    if existing is not None:
        return existing
    row = handoff_repo.add_artifact_link(
        db,
        artifact_id=int(artifact_id),
        task_id=int(task.id),
        role=ArtifactLinkRole.consumed_by.value,
        work_session_id=None,
        actor_employee_id=actor_employee_id,
        reason=reason,
        revision_id=artifact_ref(db, node).revision_id,
    )
    if commit:
        db.commit()
    return row


# ---------------------------------------------------------------------------
# 报告与 lineage（G2/H7）
# ---------------------------------------------------------------------------


def upstream_lineage(db: Session, *, task_id: int, max_hops: int = 6) -> tuple[LineageHop, ...]:
    """沿"消费关系"往回走，把上游 Task + 它的产物列出来（≥2 跳）。

    两个来源合起来才是完整的输入链：

    - **声明**（`task_inputs`）：Manager 计划好的上游；
    - **使用事实**（`artifact_links`）：实际用掉的产物由哪个 Task 产出。

    环由 `seen` 集合兜住（坏数据不许把报告接口转成死循环）。
    """
    hops: list[LineageHop] = []
    seen: set[int] = {int(task_id)}
    frontier: list[tuple[int, int]] = []  # (source_task_id, depth)

    def _push(source_id: int, depth: int) -> None:
        if source_id in seen or depth > max_hops:
            return
        seen.add(source_id)
        frontier.append((source_id, depth))

    for item in declared_inputs(db, int(task_id)):
        _push(item.source_task_id, 1)
    for link in handoff_repo.list_artifact_links(db, task_id=int(task_id)):
        node = drive_repo.get_node(db, int(link.artifact_id))
        if node is not None and node.task_id is not None:
            _push(int(node.task_id), 1)

    while frontier:
        source_id, depth = frontier.pop(0)
        source = project_repo.get_task(db, source_id)
        for node in artifact_versions.current_nodes(db, source_id):
            hops.append(
                LineageHop(
                    depth=depth,
                    task_id=source_id,
                    task_title=source.title if source else "",
                    task_status=source.status if source else "",
                    artifact_id=int(node.id),
                    artifact_title=node.name,
                    doc_type=node.doc_type or "",
                )
            )
        for item in declared_inputs(db, source_id):
            _push(item.source_task_id, depth + 1)
        for link in handoff_repo.list_artifact_links(db, task_id=source_id):
            node = drive_repo.get_node(db, int(link.artifact_id))
            if node is not None and node.task_id is not None:
                _push(int(node.task_id), depth + 1)
    return tuple(sorted(hops, key=lambda hop: (hop.depth, hop.task_id, hop.artifact_id)))


def consumed_artifacts(db: Session, task_id: int) -> tuple[ConsumedRef, ...]:
    out: list[ConsumedRef] = []
    links = handoff_repo.list_artifact_links(db, task_id=int(task_id))
    used = {link.artifact_id for link in links if link.work_session_id is not None}
    for link in links:
        if link.work_session_id is None and link.artifact_id in used:
            continue
        node = drive_repo.get_node(db, int(link.artifact_id))
        if node is None:
            continue
        owner = project_repo.get_task(db, int(node.task_id)) if node.task_id else None
        revision = db.get(DriveRevision, link.revision_id) if link.revision_id else None
        out.append(
            ConsumedRef(
                artifact_id=int(node.id),
                title=node.name,
                doc_type=node.doc_type or "",
                task_id=int(task_id),
                task_title=owner.title if owner else None,
                work_session_id=int(link.work_session_id) if link.work_session_id else None,
                actor_employee_id=(int(link.actor_employee_id) if link.actor_employee_id else None),
                reason=link.reason,
                created_at=link.created_at.isoformat() if link.created_at else "",
                revision_id=int(revision.id) if revision else None,
                version=int(revision.version) if revision else None,
                sha256=revision.sha256 if revision else None,
            )
        )
    return tuple(out)


def task_artifact_report(db: Session, task_id: int) -> TaskArtifactReport:
    """一个 Task 的交付物全景：产出 / 使用 / 声明 / 上游链（G2）。"""
    task = project_repo.get_task(db, int(task_id))
    if task is None:
        raise LineageViolation(f"task {task_id} not found")
    return TaskArtifactReport(
        task_id=int(task.id),
        produced=produced_artifacts(db, int(task.id)),
        consumed=consumed_artifacts(db, int(task.id)),
        declared_inputs=declared_inputs(db, int(task.id)),
        inputs=resolve_input_artifacts(db, task),
        upstream=upstream_lineage(db, task_id=int(task.id)),
        missing_input_sources=missing_input_sources(db, task),
        produces=tuple(task.produces_json or ()),
        extra={
            "project_id": int(task.project_id),
            "self_artifacts_are_consumable": str(task.status) == TaskStatus.done.value,
        },
    )


def declared_artifact_types(task: Task) -> tuple[str, ...]:
    """`produces` 声明的类型（去重、保序）。"""
    return tuple(dict.fromkeys(str(item) for item in (task.produces_json or ())))


__all__ = [
    "ArtifactRef",
    "ConsumedRef",
    "DeclaredInput",
    "InputArtifactRef",
    "LineageHop",
    "LineageViolation",
    "TaskArtifactReport",
    "ancestor_task_ids",
    "consume_artifact",
    "consumed_artifacts",
    "declared_artifact_types",
    "declared_inputs",
    "declare_inputs",
    "missing_input_sources",
    "produced_artifacts",
    "record_consumed",
    "resolve_input_artifacts",
    "task_artifact_report",
    "upstream_lineage",
    "validate_input_sources",
    "DriveZone",
]
