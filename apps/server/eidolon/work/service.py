"""Work service: builds the facts a caller may write *after* checking state and,
for management transitions, the decision that authorises them.

A decision is accepted only if it is ``approved``, at the right gate, and its
subject names this very project/task (and, for assignment, this assignee). The
system checks; it never chooses (R1, R2, R4, R10, RV1, RV7).
"""

from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from eidolon.institution import decisions as D
from eidolon.kernel.facts import Actor, Fact, NewFact
from eidolon.kernel.store import FactStore
from eidolon.work import facts as F
from eidolon.work import graph
from eidolon.work.state import MissionState, ProjectState


class WorkService:
    def __init__(self, store: FactStore | None = None):
        self.store = store or FactStore()

    # -- reads --------------------------------------------------------------------

    def project(self, db: Session, project_id: UUID) -> ProjectState:
        return ProjectState.from_facts(
            project_id, self.store.by_correlation(db, project_id, kinds=F.PROJECT_KINDS)
        )

    def mission(self, db: Session, mission_id: UUID) -> MissionState:
        return MissionState.from_facts(
            mission_id, self.store.by_correlation(db, mission_id, kinds=F.MISSION_KINDS)
        )

    def decision(self, db: Session, decision_id: UUID, *, gate: str, **subject) -> D.DecisionState:
        """An approved decision at ``gate`` whose subject carries ``subject``."""
        state = D.load(db, decision_id, store=self.store)
        if state.gate != gate:
            raise F.WorkError(f"decision {decision_id} is for gate {state.gate}, not {gate}")
        if state.status != "approved":
            raise F.WorkError(f"decision {decision_id} is {state.status}, not approved")
        for key, value in subject.items():
            if str(state.subject.get(key)) != str(value):
                raise F.WorkError(
                    f"decision {decision_id} is about {key}={state.subject.get(key)!r}, "
                    f"not {value!r}"
                )
        return state

    def write(self, db: Session, fact: NewFact) -> Fact:
        return self.store.append(db, fact)

    # -- mission ------------------------------------------------------------------

    def create_mission(self, db, company_id: UUID, actor: Actor, **fields) -> Fact:
        mission_id = fields.pop("mission_id", None) or uuid4()
        return self.write(db, F.mission_created(company_id, actor, mission_id, **fields))

    def decide_mission(
        self, db, mission_id: UUID, actor: Actor, *, decision_id: UUID, reason: str | None = None
    ) -> Fact:
        state = self.mission(db, mission_id)
        if state.status != "proposed":
            raise F.WorkError(f"mission {mission_id} is already {state.status}")
        decision = D.load(db, decision_id, store=self.store)
        if decision.gate != "mission.accept" or decision.subject.get("mission_id") != str(
            mission_id
        ):
            raise F.WorkError(
                f"decision {decision_id} is not a mission.accept decision for {mission_id}"
            )
        if decision.status == "pending":
            raise F.WorkError(f"decision {decision_id} is still pending")
        accepted = decision.status == "approved"
        return self.write(
            db,
            F.mission_decided(
                state.company_id,
                actor,
                mission_id,
                accepted=accepted,
                decision_id=decision_id,
                reason=reason,
                causation_id=state.last_fact_id,
            ),
        )

    # -- project & plan -------------------------------------------------------------

    def create_project(
        self,
        db,
        company_id: UUID,
        actor: Actor,
        *,
        title: str,
        budget: int,
        mission_id: UUID | None = None,
        manager: str | None = None,
        project_id: UUID | None = None,
    ) -> Fact:
        if mission_id is not None:
            m = self.mission(db, mission_id)
            if m.status != "accepted":
                raise F.WorkError(
                    f"mission {mission_id} is {m.status}; only accepted missions get projects"
                )
            if m.budget is not None and budget > m.budget:
                raise F.WorkError("project budget exceeds the mission budget")
        return self.write(
            db,
            F.project_created(
                company_id,
                actor,
                project_id or uuid4(),
                mission_id=mission_id,
                title=title,
                budget=budget,
                manager=manager,
            ),
        )

    def create_task(
        self, db, project_id: UUID, actor: Actor, *, task_id: UUID | None = None, **fields
    ) -> Fact:
        state = self.project(db, project_id)
        if state.status in ("delivered", "cancelled"):
            raise F.WorkError(f"project {project_id} is {state.status}")
        task_id = task_id or uuid4()
        for dep in (*fields.get("depends_on", ()), *fields.get("inputs", ())):
            if dep not in state.tasks:
                raise F.WorkError(f"dependency {dep} is not a task of project {project_id}")
        fact = F.task_created(
            state.company_id, actor, project_id, task_id, causation_id=state.last_fact_id, **fields
        )
        # the graph after this task must still be a DAG with DAG-guaranteed inputs (H5)
        nodes = state.nodes() + [
            graph.Node(
                task_id,
                "todo",
                tuple(fields.get("depends_on", ())),
                tuple(fields.get("inputs", ())),
            )
        ]
        report = graph.validate(nodes)
        if not report.is_valid:
            raise graph.DagError(report.error)
        return self.write(db, fact)

    def approve_plan(self, db, project_id: UUID, actor: Actor, *, decision_id: UUID) -> Fact:
        state = self.project(db, project_id)
        if state.status != "planning":
            raise F.WorkError(f"project {project_id} is {state.status}")
        if not state.tasks:
            raise F.WorkError("a plan needs at least one task")
        report = graph.validate(state.nodes())
        if not report.is_valid:
            raise graph.DagError(report.error)
        self.decision(db, decision_id, gate="plan.approve", project_id=project_id)
        return self.write(
            db,
            F.project_plan_approved(
                state.company_id,
                actor,
                project_id,
                decision_id=decision_id,
                task_ids=list(state.tasks),
                causation_id=state.last_fact_id,
            ),
        )

    def deliver_project(self, db, project_id: UUID, actor: Actor, *, decision_id: UUID) -> Fact:
        state = self.project(db, project_id)
        if state.status != "active":
            raise F.WorkError(f"project {project_id} is {state.status}")
        undone = [t for t in state.tasks.values() if t.status not in ("done", "cancelled")]
        if undone:
            raise F.WorkError(f"{len(undone)} tasks are not done")
        self.decision(db, decision_id, gate="project.deliver", project_id=project_id)
        return self.write(
            db,
            F.project_delivered(
                state.company_id,
                actor,
                project_id,
                decision_id=decision_id,
                causation_id=state.last_fact_id,
            ),
        )

    def cancel_project(
        self, db, project_id: UUID, actor: Actor, *, decision_id: UUID, reason: str
    ) -> Fact:
        state = self.project(db, project_id)
        if state.status in ("delivered", "cancelled"):
            raise F.WorkError(f"project {project_id} is {state.status}")
        self.decision(db, decision_id, gate="task.cancel", project_id=project_id)
        return self.write(
            db,
            F.project_cancelled(
                state.company_id,
                actor,
                project_id,
                decision_id=decision_id,
                reason=reason,
                causation_id=state.last_fact_id,
            ),
        )

    # -- task lifecycle ----------------------------------------------------------------

    def _task(self, db, project_id: UUID, task_id: UUID, allowed_kind: str):
        state = self.project(db, project_id)
        task = state.task(task_id)
        allowed = F.TASK_TRANSITIONS[allowed_kind]
        if task.status not in allowed:
            raise F.WorkError(
                f"task {task_id} is {task.status}; {allowed_kind} needs one of {sorted(allowed)}"
            )
        return state, task

    def assign_task(
        self, db, project_id: UUID, task_id: UUID, actor: Actor, *, assignee: str, decision_id: UUID
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_ASSIGNED)
        if state.status != "active":
            raise F.WorkError(
                f"project {project_id} is {state.status}; assignment needs an approved plan"
            )
        self.decision(db, decision_id, gate="task.assign", task_id=task_id, assignee=assignee)
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_ASSIGNED,
                {"assignee": assignee, "decision_id": str(decision_id), "previous": task.assignee},
                f"assigned:{decision_id}",
                decision_id=decision_id,
                causation_id=task.last_fact_id,
            ),
        )

    def start_task(
        self, db, project_id: UUID, task_id: UUID, actor: Actor, *, run_id: UUID, person_id: str
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_STARTED)
        if task.assignee is None:
            raise F.WorkError(
                f"task {task_id} has no assignee "
                "(R4: a ready unassigned task is a management decision)"
            )
        if person_id != task.assignee:
            raise F.WorkError(
                f"task {task_id} is assigned to {task.assignee}, not {person_id} (R1)"
            )
        if not graph.is_ready({n.task_id: n for n in state.nodes()}, task_id):
            raise F.WorkError(f"task {task_id} is not ready: dependencies or inputs are not done")
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_STARTED,
                {"run_id": str(run_id), "person_id": person_id, "attempt": len(task.runs) + 1},
                f"started:{run_id}",
                causation_id=task.last_fact_id,
            ),
        )

    def submit_task(
        self,
        db,
        project_id: UUID,
        task_id: UUID,
        actor: Actor,
        *,
        run_id: UUID,
        artifact_ids: Sequence[UUID] = (),
        summary: str = "",
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_SUBMITTED)
        if task.current_run_id != run_id:
            raise F.WorkError(f"run {run_id} is not the task's current run")
        for aid in artifact_ids:
            art = state.artifacts.get(aid)
            if art is None or art.task_id != task_id:
                raise F.WorkError(f"artifact {aid} was not produced by task {task_id} (H2)")
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_SUBMITTED,
                {
                    "run_id": str(run_id),
                    "artifact_ids": [str(a) for a in artifact_ids],
                    "summary": summary[:2000],
                },
                f"submitted:{run_id}",
                causation_id=task.last_fact_id,
            ),
        )

    def review_task(
        self,
        db,
        project_id: UUID,
        task_id: UUID,
        actor: Actor,
        *,
        decision_id: UUID,
        verdict: str,
        reviewer: str,
        reason: str | None = None,
    ) -> Fact:
        if verdict not in F.REVIEW_VERDICTS:
            raise F.WorkError(f"verdict must be one of {F.REVIEW_VERDICTS}")
        state, task = self._task(db, project_id, task_id, F.TASK_REVIEWED)
        decision = D.load(db, decision_id, store=self.store)
        if decision.gate != "task.review" or str(decision.subject.get("task_id")) != str(task_id):
            raise F.WorkError(f"decision {decision_id} is not a task.review decision for {task_id}")
        if decision.status == "pending":
            raise F.WorkError("the review decision is still pending (RV2: no auto-approval)")
        expected = {"approved": "pass", "rework": "rework", "rejected": "escalate"}[decision.status]
        if verdict != expected:
            raise F.WorkError(
                f"decision {decision_id} is {decision.status}, which maps to {expected!r}, "
                f"not {verdict!r} (RV8)"
            )
        if not any(s.person_id == reviewer for s in decision.signatures):
            raise F.WorkError(f"{reviewer} did not sign decision {decision_id} (RV4)")
        if reviewer == task.assignee:
            raise F.WorkError("the author may not review their own task")
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_REVIEWED,
                {
                    "decision_id": str(decision_id),
                    "verdict": verdict,
                    "reviewer": reviewer,
                    "reason": reason,
                    "rework_count": task.rework_count + (verdict == "rework"),
                },
                f"reviewed:{decision_id}",
                decision_id=decision_id,
                causation_id=task.last_fact_id,
            ),
        )

    def block_task(self, db, project_id: UUID, task_id: UUID, actor: Actor, *, reason: str) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_BLOCKED)
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_BLOCKED,
                {"reason": reason},
                f"blocked:{(task.last_fact_id or '')}",
                causation_id=task.last_fact_id,
            ),
        )

    def unblock_task(
        self,
        db,
        project_id: UUID,
        task_id: UUID,
        actor: Actor,
        *,
        reason: str,
        decision_id: UUID | None = None,
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_UNBLOCKED)
        if decision_id is None and actor.kind.value != "human":
            raise F.WorkError("unblocking needs a decision_id unless a human does it")
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_UNBLOCKED,
                {"reason": reason, "decision_id": str(decision_id) if decision_id else None},
                f"unblocked:{task.last_fact_id}",
                decision_id=decision_id,
                causation_id=task.last_fact_id,
            ),
        )

    def fail_task(
        self, db, project_id: UUID, task_id: UUID, actor: Actor, *, run_id: UUID, reason: str
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_FAILED)
        if task.current_run_id != run_id:
            raise F.WorkError(f"run {run_id} is not the task's current run")
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_FAILED,
                {"run_id": str(run_id), "reason": reason},
                f"failed:{run_id}",
                causation_id=task.last_fact_id,
            ),
        )

    def reopen_task(
        self, db, project_id: UUID, task_id: UUID, actor: Actor, *, decision_id: UUID, reason: str
    ) -> Fact:
        """R5/R10: a failed task returns to work only through a decision; the system
        never reassigns or retries on its own."""
        state, task = self._task(db, project_id, task_id, F.TASK_REOPENED)
        self.decision(db, decision_id, gate="task.assign", task_id=task_id)
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_REOPENED,
                {"decision_id": str(decision_id), "reason": reason},
                f"reopened:{decision_id}",
                decision_id=decision_id,
                causation_id=task.last_fact_id,
            ),
        )

    def cancel_task(
        self, db, project_id: UUID, task_id: UUID, actor: Actor, *, decision_id: UUID, reason: str
    ) -> Fact:
        state, task = self._task(db, project_id, task_id, F.TASK_CANCELLED)
        self.decision(db, decision_id, gate="task.cancel", task_id=task_id)
        return self.write(
            db,
            F.task_event(
                state.company_id,
                actor,
                project_id,
                task_id,
                F.TASK_CANCELLED,
                {"decision_id": str(decision_id), "reason": reason},
                "cancelled",
                decision_id=decision_id,
                causation_id=task.last_fact_id,
            ),
        )

    # -- artifacts -----------------------------------------------------------------------

    def produce_artifact(
        self,
        db,
        project_id: UUID,
        task_id: UUID,
        actor: Actor,
        *,
        run_id: UUID | None,
        path: str,
        blob_ref: str,
        size: int,
        media_type: str = "text/plain",
        artifact_id: UUID | None = None,
    ) -> Fact:
        state = self.project(db, project_id)
        task = state.task(task_id)
        if task.status != "in_progress":
            raise F.WorkError(
                f"task {task_id} is {task.status}; only running tasks produce artifacts"
            )
        if run_id is not None and task.current_run_id != run_id:
            raise F.WorkError(f"run {run_id} is not the task's current run")
        return self.write(
            db,
            F.artifact_produced(
                state.company_id,
                actor,
                project_id,
                artifact_id or uuid4(),
                task_id=task_id,
                run_id=run_id,
                path=path,
                blob_ref=blob_ref,
                size=size,
                media_type=media_type,
                causation_id=task.last_fact_id,
            ),
        )

    def consume_artifact(
        self,
        db,
        project_id: UUID,
        artifact_id: UUID,
        actor: Actor,
        *,
        by_task_id: UUID,
        run_id: UUID | None,
    ) -> Fact:
        state = self.project(db, project_id)
        art = state.artifacts.get(artifact_id)
        if art is None:
            raise F.WorkError(f"no artifact {artifact_id} in project {project_id}")
        producer = state.task(art.task_id)
        consumer = state.task(by_task_id)
        if producer.status != "done":
            raise F.WorkError(
                f"artifact {artifact_id}'s task is {producer.status}; "
                "only finished work is consumable (H8)"
            )
        if art.task_id not in consumer.inputs and art.task_id not in graph.ancestors(
            {n.task_id: n for n in state.nodes()}, by_task_id
        ):
            raise F.WorkError(
                f"task {by_task_id} does not declare {art.task_id} as an input or ancestor (H4/H5)"
            )
        return self.write(
            db,
            F.artifact_consumed(
                state.company_id,
                actor,
                project_id,
                artifact_id,
                by_task_id=by_task_id,
                run_id=run_id,
                causation_id=consumer.last_fact_id,
            ),
        )

    def inputs_for(self, db, project_id: UUID, task_id: UUID) -> list:
        """H4: the artifacts a task's declared input producers have made (resolved at run time)."""
        state = self.project(db, project_id)
        task = state.task(task_id)
        return [
            a
            for a in state.artifacts.values()
            if a.task_id in task.inputs and state.tasks[a.task_id].status == "done"
        ]
