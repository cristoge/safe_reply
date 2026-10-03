"""Proveedores de dependencias: construyen repositorios y servicios por petición.

FastAPI guarda en caché cada dependencia durante una misma petición: aunque
get_db aparezca en varios sitios de la cadena, se ejecuta una sola vez, así que
el repositorio y el servicio comparten la misma sesión (y la misma transacción).
"""

from typing import Annotated

from fastapi import Depends

from revision_rag.db.session import DbSession
from revision_rag.repositories.user import UserRepository
from revision_rag.services.auth import AuthService
from revision_rag.services.user import UserService


def get_user_repository(db: DbSession) -> UserRepository:
    return UserRepository(db)


def get_auth_service(
    db: DbSession,
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> AuthService:
    return AuthService(db, users)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]


def get_user_service(
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserService:
    return UserService(users)


UserServiceDep = Annotated[UserService, Depends(get_user_service)]
