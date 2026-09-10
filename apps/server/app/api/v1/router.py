"""Aggregate router: one file per resource. See docs/architecture.md §8."""

from fastapi import APIRouter, Depends

from app.api.dependencies import require_user
from app.api.v1 import (
    artifacts,
    assessment,
    auth,
    behavior,
    career,
    company,
    competencies,
    cultivation,
    drive,
    employees,
    git,
    knowledge,
    learning_api,
    lifecycle,
    messages,
    meta,
    organizations,
    position_candidates,
    position_fit,
    position_profiles,
    project_delivery,
    projects,
    providers,
    runtimes,
    talent_roster,
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
protected.include_router(behavior.router)
protected.include_router(competencies.router)
protected.include_router(cultivation.router)
protected.include_router(assessment.router)
protected.include_router(career.router)
protected.include_router(providers.router)
protected.include_router(git.router)
protected.include_router(runtimes.router)
protected.include_router(lifecycle.router)
protected.include_router(learning_api.router)
protected.include_router(organizations.router)
protected.include_router(position_profiles.router)
protected.include_router(position_fit.router)
protected.include_router(position_candidates.router)
protected.include_router(talent_roster.router)
protected.include_router(meta.router)
protected.include_router(tutorial.router)
protected.include_router(tutorial.practice_router)
api_router.include_router(protected)
