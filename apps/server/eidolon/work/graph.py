"""Pure task-graph functions. They describe structure and readiness; they never
pick anyone or plan anything (R2, R3, R12, RD1)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from uuid import UUID


class DagError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Node:
    task_id: UUID
    status: str
    depends_on: tuple[UUID, ...] = ()
    inputs: tuple[UUID, ...] = ()  # producer tasks whose artifacts this task consumes (H4)
    assignee: str | None = None


@dataclass(frozen=True, slots=True)
class GraphReport:
    is_valid: bool
    self_loops: tuple[UUID, ...] = ()
    dangling: tuple[tuple[UUID, UUID], ...] = ()
    duplicates: tuple[tuple[UUID, UUID], ...] = ()
    cycles: tuple[tuple[UUID, ...], ...] = ()
    inputs_not_ancestors: tuple[tuple[UUID, UUID], ...] = ()  # H5

    @property
    def error(self) -> str:
        parts = []
        if self.self_loops:
            parts.append(f"self loops: {[str(t) for t in self.self_loops]}")
        if self.dangling:
            parts.append(f"dangling: {[(str(a), str(b)) for a, b in self.dangling]}")
        if self.duplicates:
            parts.append(f"duplicate edges: {[(str(a), str(b)) for a, b in self.duplicates]}")
        if self.cycles:
            parts.append(f"cycles: {[[str(t) for t in c] for c in self.cycles]}")
        if self.inputs_not_ancestors:
            parts.append(
                "inputs not guaranteed by the DAG: "
                f"{[(str(a), str(b)) for a, b in self.inputs_not_ancestors]}"
            )
        return "; ".join(parts)


def validate(nodes: Iterable[Node]) -> GraphReport:
    by_id = {n.task_id: n for n in nodes}
    self_loops, dangling, duplicates, bad_inputs = [], [], [], []
    for n in by_id.values():
        seen = set()
        for dep in n.depends_on:
            if dep == n.task_id:
                self_loops.append(n.task_id)
            elif dep not in by_id:
                dangling.append((n.task_id, dep))
            if dep in seen:
                duplicates.append((n.task_id, dep))
            seen.add(dep)
    cycles = _cycles(
        {t: tuple(d for d in n.depends_on if d in by_id and d != t) for t, n in by_id.items()}
    )
    if not (self_loops or dangling or cycles):
        for n in by_id.values():
            anc = ancestors(by_id, n.task_id)
            for producer in n.inputs:
                if producer not in anc:
                    bad_inputs.append((n.task_id, producer))
    ok = not (self_loops or dangling or duplicates or cycles or bad_inputs)
    return GraphReport(
        ok, tuple(self_loops), tuple(dangling), tuple(duplicates), cycles, tuple(bad_inputs)
    )


def _cycles(adj: Mapping[UUID, tuple[UUID, ...]]) -> tuple[tuple[UUID, ...], ...]:
    """Tarjan: every strongly connected component of size > 1 is a cycle."""
    index: dict[UUID, int] = {}
    low: dict[UUID, int] = {}
    stack: list[UUID] = []
    on_stack: set[UUID] = set()
    out: list[tuple[UUID, ...]] = []
    counter = [0]

    def visit(v: UUID) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                out.append(tuple(sorted(comp, key=str)))

    for v in adj:
        if v not in index:
            visit(v)
    return tuple(sorted(out, key=lambda c: str(c[0])))


def ancestors(by_id: Mapping[UUID, Node], task_id: UUID) -> set[UUID]:
    out: set[UUID] = set()
    frontier = list(by_id[task_id].depends_on) if task_id in by_id else []
    while frontier:
        t = frontier.pop()
        if t in out or t not in by_id:
            continue
        out.add(t)
        frontier.extend(by_id[t].depends_on)
    return out


def is_ready(by_id: Mapping[UUID, Node], task_id: UUID) -> bool:
    """RD1/H5/H8: ``todo`` and every dependency and input producer is ``done``.
    Readiness says nothing about who should do it (R3)."""
    n = by_id[task_id]
    if n.status != "todo":
        return False
    for dep in (*n.depends_on, *n.inputs):
        producer = by_id.get(dep)
        if producer is None or producer.status != "done":
            return False
    return True


def resolve_ready(nodes: Iterable[Node]) -> list[Node]:
    by_id = {n.task_id: n for n in nodes}
    return [n for n in by_id.values() if is_ready(by_id, n.task_id)]


def dispatchable(nodes: Iterable[Node]) -> list[Node]:
    """R1/R3: ready *and* already assigned. A ready, unassigned task is a
    management question (R4), never something the system fills in."""
    return [n for n in resolve_ready(nodes) if n.assignee]


def awaiting_assignment(nodes: Iterable[Node]) -> list[Node]:
    return [n for n in resolve_ready(nodes) if not n.assignee]


def lineage(
    produced_by: Mapping[UUID, UUID],
    consumed: Mapping[UUID, tuple[UUID, ...]],
    artifact_id: UUID,
    *,
    depth: int = 10,
) -> list[tuple[UUID, UUID]]:
    """H7: walk back from an artifact: (artifact, producer task) pairs, following
    what the producer task itself consumed."""
    out: list[tuple[UUID, UUID]] = []
    frontier = [artifact_id]
    seen: set[UUID] = set()
    while frontier and depth > 0:
        depth -= 1
        nxt: list[UUID] = []
        for art in frontier:
            if art in seen or art not in produced_by:
                continue
            seen.add(art)
            producer = produced_by[art]
            out.append((art, producer))
            nxt.extend(consumed.get(producer, ()))
        frontier = nxt
    return out
