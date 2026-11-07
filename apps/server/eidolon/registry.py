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
    from eidolon.work import WorkProjection

    registry = Registry()
    # work layer (R3): missions / projects / tasks / artifacts read model, run launching.
    registry.register(WorkProjection())
    registry.register(RunLauncher())
    # agent layer (R2): the memory read model and the reflection that feeds it.
    registry.register(MemoryProjection())
    blobs = BlobStore(SERVER_ROOT / settings.transcript_dir)
    registry.register(Reflector(build_provider(settings, session_factory=SessionLocal), blobs))
    # identity / institution / work / money / talent register here as each is ported.
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
    return [
        TaskRunExecutor(
            factory, provider, blobs, workspaces, memory=MemoryRecall(factory, MemoryRepository())
        ),
        ReviewExecutor(
            factory,
            provider,
            blobs,
            workspaces,
            reviewer=settings.reviewer_person,
            role=settings.reviewer_role,
        ),
    ]
