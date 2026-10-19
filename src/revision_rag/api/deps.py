"""Proveedores de dependencias: construyen repositorios y servicios por petición.

FastAPI guarda en caché cada dependencia durante una misma petición: aunque
get_db aparezca en varios sitios de la cadena, se ejecuta una sola vez, así que
el repositorio y el servicio comparten la misma sesión (y la misma transacción).
"""

import uuid
from typing import Annotated

from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from revision_rag.clients.classifier import IntentClassifier
from revision_rag.core.config import Settings, get_settings
from revision_rag.core.exceptions import ForbiddenError, NotAuthenticatedError
from revision_rag.db.cache import RedisClient
from revision_rag.db.session import DbSession
from revision_rag.models import Ticket, User
from revision_rag.models.enums import UserRole
from revision_rag.repositories.refresh_token import RefreshTokenRepository
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.pagination import PaginationParams
from revision_rag.services.auth import AuthService
from revision_rag.services.rate_limiter import RateLimiter
from revision_rag.services.ticket import TicketService
from revision_rag.services.ticket_processor import TicketProcessor
from revision_rag.services.user import UserService
from revision_rag.tasks.queue import InlineTaskQueue, TaskQueue
from revision_rag.triage.answers import CannedAnswers
from revision_rag.triage.rules import Rules

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


# El rol se comprueba aquí y no en cada servicio: la ruta lo declara con el alias
# (AgentUser / CustomerUser) y la comprobación está en un único sitio.
async def require_agent(user: CurrentUser) -> User:
    if user.role != UserRole.AGENT:
        raise ForbiddenError
    return user


AgentUser = Annotated[User, Depends(require_agent)]


async def require_customer(user: CurrentUser) -> User:
    # Los tickets son siempre de un cliente: un agente no abre tickets.
    if user.role != UserRole.CUSTOMER:
        raise ForbiddenError
    return user


CustomerUser = Annotated[User, Depends(require_customer)]


def get_ticket_rate_limiter(redis: RedisClient, settings: SettingsDep) -> RateLimiter:
    return RateLimiter(
        redis,
        limit=settings.ticket_rate_limit,
        window_seconds=settings.ticket_rate_window_seconds,
    )


# Reglas, modelo y respuestas se cargan una vez al arrancar (lifespan, main.py)
# y se comparten entre peticiones. En los tests se sustituyen (modelo falso).
def get_rules(request: Request) -> Rules:
    return request.app.state.rules


def get_classifier(request: Request) -> IntentClassifier:
    return request.app.state.classifier


def get_canned_answers(request: Request) -> CannedAnswers:
    return request.app.state.canned_answers


def get_ticket_processor(
    db: DbSession,
    rules: Annotated[Rules, Depends(get_rules)],
    classifier: Annotated[IntentClassifier, Depends(get_classifier)],
    answers: Annotated[CannedAnswers, Depends(get_canned_answers)],
) -> TicketProcessor:
    return TicketProcessor(
        db,
        TicketRepository(db),
        TicketMessageRepository(db),
        TicketEventRepository(db),
        classifier,
        rules,
        answers,
    )


def get_task_queue(
    processor: Annotated[TicketProcessor, Depends(get_ticket_processor)],
) -> TaskQueue:
    # v1: en la misma petición. En la v2 aquí se devolverá un ArqTaskQueue.
    return InlineTaskQueue(processor.process_ticket)


def get_ticket_service(
    db: DbSession,
    rate_limiter: Annotated[RateLimiter, Depends(get_ticket_rate_limiter)],
    queue: Annotated[TaskQueue, Depends(get_task_queue)],
) -> TicketService:
    return TicketService(
        db,
        TicketRepository(db),
        TicketMessageRepository(db),
        TicketEventRepository(db),
        rate_limiter,
        queue,
    )


TicketServiceDep = Annotated[TicketService, Depends(get_ticket_service)]


async def get_owned_ticket(
    ticket_id: uuid.UUID, customer: CustomerUser, service: TicketServiceDep
) -> Ticket:
    """El ticket del {ticket_id} de la URL, solo si es del cliente (si no, 404).

    La reutilizarán todas las rutas del cliente que cuelgan de un ticket
    (mensajes, escalar en el S5): la comprobación del dueño está en un único sitio.
    """
    return await service.get(customer, ticket_id)


OwnedTicket = Annotated[Ticket, Depends(get_owned_ticket)]
