from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, status

from revision_rag.api.deps import (
    CurrentUser,
    OwnedProject,
    Pagination,
    ProjectServiceDep,
)
from revision_rag.core.exceptions import NotAuthenticatedError, ProjectNotFoundError
from revision_rag.models import Project
from revision_rag.schemas.project import ProjectCreate, ProjectRead, ProjectUpdate

router = APIRouter(
    prefix="/projects",
    tags=["projects"],
    responses={
        NotAuthenticatedError.status_code: {"description": NotAuthenticatedError.detail}
    },
)

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    ProjectNotFoundError.status_code: {"description": ProjectNotFoundError.detail}
}


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectRead)
async def create_project(
    data: ProjectCreate, user: CurrentUser, service: ProjectServiceDep
) -> Project:
    return await service.create(user, data)


@router.get("", response_model=list[ProjectRead])
async def list_projects(
    user: CurrentUser, service: ProjectServiceDep, pagination: Pagination
) -> Sequence[Project]:
    return await service.list_projects(
        user, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{project_id}", response_model=ProjectRead, responses=NOT_FOUND)
async def read_project(project: OwnedProject) -> Project:
    return project


@router.patch("/{project_id}", response_model=ProjectRead, responses=NOT_FOUND)
async def update_project(
    data: ProjectUpdate, project: OwnedProject, service: ProjectServiceDep
) -> Project:
    return await service.update(project, data)


@router.delete(
    "/{project_id}", status_code=status.HTTP_204_NO_CONTENT, responses=NOT_FOUND
)
async def delete_project(project: OwnedProject, service: ProjectServiceDep) -> None:
    await service.delete(project)
