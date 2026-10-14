"""Proveedores de dependencias: construyen repositorios y servicios por petición.

FastAPI guarda en caché cada dependencia durante una misma petición: aunque
get_db aparezca en varios sitios de la cadena, se ejecuta una sola vez, así que
el repositorio y el servicio comparten la misma sesión (y la misma transacción).
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from revision_rag.core.config import Settings, get_settings
from revision_rag.core.exceptions import NotAuthenticatedError
from revision_rag.db.session import DbSession
from revision_rag.models import Project, User
from revision_rag.repositories.project import ProjectRepository
from revision_rag.repositories.refresh_token import RefreshTokenRepository
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.pagination import PaginationParams
from revision_rag.services.auth import AuthService
from revision_rag.services.project import ProjectService
from revision_rag.services.user import UserService

SettingsDep = Annotated[Settings, Depends(get_settings)]

# Solo lo usan las rutas que devuelven listas. Query(): los campos del modelo
# se leen de la URL (?limit=5&offset=0) y en /docs salen como dos parámetros.
Pagination = Annotated[PaginationParams, Query()]


def get_user_repository(db: DbSession) -> UserRepository:
    return UserRepository(db)


def get_refresh_token_repository(db: DbSession) -> RefreshTokenRepository:
    return RefreshTokenRepository(db)


def get_auth_service(
    db: DbSession,
    users: Annotated[UserRepository, Depends(get_user_repository)],
    refresh_tokens: Annotated[
        RefreshTokenRepository, Depends(get_refresh_token_repository)
    ],
    settings: SettingsDep,
) -> AuthService:
    return AuthService(db, users, refresh_tokens, settings)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_user_service(
    db: DbSession,
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserService:
    return UserService(db, users)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


# Lee la cabecera "Authorization: Bearer <token>" y añade el botón "Authorize"
# en /docs. auto_error=False: si falta la cabecera, el error lo lanzamos
# nosotros, con el mismo formato que el resto de 401.
bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    service: AuthServiceDep,
) -> User:
    """El usuario dueño del access token. Cualquier ruta que lo pida queda protegida."""
    if credentials is None:
        raise NotAuthenticatedError
    return await service.get_user_from_access_token(credentials.credentials)


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_project_repository(db: DbSession) -> ProjectRepository:
    return ProjectRepository(db)


def get_project_service(
    db: DbSession,
    projects: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectService:
    return ProjectService(db, projects)


ProjectServiceDep = Annotated[ProjectService, Depends(get_project_service)]


async def get_owned_project(
    project_id: uuid.UUID, user: CurrentUser, service: ProjectServiceDep
) -> Project:
    """El proyecto del {project_id} de la URL, solo si es del usuario (si no, 404).

    La reutilizan todas las rutas que cuelgan de un proyecto (documentos,
    conversaciones, materiales): la comprobación del dueño está en un único sitio.
    """
    return await service.get(user, project_id)


OwnedProject = Annotated[Project, Depends(get_owned_project)]
