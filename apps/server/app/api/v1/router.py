"""Aggregate router: one file per resource. See docs/architecture.md §8."""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_user
from app.api.v1 import (
    artifacts,
    auth,
    company,
    drive,
    employees,
    git,
    knowledge,
    lifecycle,
    messages,
    meta,
    project_delivery,
    projects,
    providers,
    runtimes,
    tasks,
    tutorial,
)

api_router = APIRouter()
api_router.include_router(auth.router)
protected = APIRouter(dependencies=[Depends(require_user)])
protected.include_router(company.router)
protected.include_router(employees.router)
protected.include_router(projects.router)
protected.include_router(project_delivery.router)
protected.include_router(tasks.router)
protected.include_router(artifacts.router)
protected.include_router(drive.router)
protected.include_router(messages.router)
protected.include_router(knowledge.router)
protected.include_router(providers.router)
protected.include_router(git.router)
protected.include_router(runtimes.router)
protected.include_router(lifecycle.router)
protected.include_router(meta.router)
protected.include_router(tutorial.router)
api_router.include_router(protected)
