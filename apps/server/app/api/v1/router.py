"""Aggregate router: one file per resource. See docs/architecture.md §8."""

from fastapi import APIRouter

from app.api.v1 import (
    artifacts,
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
api_router.include_router(company.router)
api_router.include_router(employees.router)
api_router.include_router(projects.router)
api_router.include_router(project_delivery.router)
api_router.include_router(tasks.router)
api_router.include_router(artifacts.router)
api_router.include_router(drive.router)
api_router.include_router(messages.router)
api_router.include_router(knowledge.router)
api_router.include_router(providers.router)
api_router.include_router(git.router)
api_router.include_router(runtimes.router)
api_router.include_router(lifecycle.router)
api_router.include_router(meta.router)
api_router.include_router(tutorial.router)
