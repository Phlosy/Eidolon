"""The consumer registry of one deployment.

Every layer above the kernel registers its projections and reactors here as it
lands. The worker and the CLI both read this, so "what consumes the fact log" has
exactly one answer. The kernel itself registers nothing.
"""

from eidolon.kernel.consumers import Registry


def default_registry() -> Registry:
    from eidolon.agent.blobs import BlobStore
    from eidolon.agent.memory import MemoryProjection, Reflector
    from eidolon.agent.providers import SERVER_ROOT, build_provider
    from eidolon.agent.scheduler import RunLauncher
    from eidolon.config import settings
    from eidolon.db import SessionLocal
    from eidolon.identity import IdentityProjection
    from eidolon.money import CostMeter, LedgerProjection
    from eidolon.observability import MetricsConsumer
    from eidolon.talent import TalentProjection
    from eidolon.work import WorkProjection

    registry = Registry()
    # identity layer (R4): users / companies / persons / employments read model.
    registry.register(IdentityProjection())
    # work layer (R3): missions / projects / tasks / artifacts read model, run launching.
    registry.register(WorkProjection())
    registry.register(RunLauncher())
    # agent layer (R2): the memory read model and the reflection that feeds it.
    registry.register(MemoryProjection())
    blobs = BlobStore(SERVER_ROOT / settings.transcript_dir)
    registry.register(Reflector(build_provider(settings, session_factory=SessionLocal), blobs))
    # money layer (R4-b): every model call is metered and booked; the ledger read model.
    registry.register(CostMeter())
    registry.register(LedgerProjection())
    # talent layer (R5-a): evidence and competencies, read-only for people.
    registry.register(TalentProjection())
    # observability (R6-c): counters off the fact stream, from the head.
    registry.register(MetricsConsumer())
    return registry


def default_jobs(session_factory=None) -> list:
    """Worker jobs (R3-d): execute task runs and reviews with the configured provider."""
    from eidolon.agent.blobs import BlobStore
    from eidolon.agent.memory import MemoryRecall, MemoryRepository
    from eidolon.agent.providers import SERVER_ROOT, build_provider
    from eidolon.agent.scheduler import ReviewExecutor, TaskRunExecutor, Workspaces
    from eidolon.config import settings
    from eidolon.db import SessionLocal

    factory = session_factory or SessionLocal
    provider = build_provider(settings, session_factory=factory)
    blobs = BlobStore(SERVER_ROOT / settings.transcript_dir)
    workspaces = Workspaces(SERVER_ROOT / settings.workspace_root)
    from eidolon.agent.escalation import EscalatedDecider, Escalator
    from eidolon.agent.managers import (
        Assigner,
        Deciders,
        IdentityRoster,
        MissionIntake,
        Planner,
        SpendApprover,
    )
    from eidolon.money.guard import BudgetGuard

    deciders = Deciders(factory, provider, blobs)
    roster = IdentityRoster()
    guard = BudgetGuard()  # money layer, injected: agent never imports money (R6-a)
    return [
        MissionIntake(factory, deciders, roster),
        Planner(factory, deciders, roster),
        Assigner(factory, deciders, roster),
        SpendApprover(factory, deciders, roster),
        Escalator(factory),
        EscalatedDecider(factory, deciders, roster),
        TaskRunExecutor(
            factory,
            provider,
            blobs,
            workspaces,
            memory=MemoryRecall(factory, MemoryRepository()),
            budget=guard,
        ),
        ReviewExecutor(
            factory,
            provider,
            blobs,
            workspaces,
            reviewer=settings.reviewer_person,
            role=settings.reviewer_role,
            budget=guard,
        ),
    ]
