import uuid
from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter, Response, status

from safe_reply.api.deps import AgentUser, HumanQueueServiceDep, Pagination
from safe_reply.core.exceptions import (
    AppError,
    ForbiddenError,
    NotAuthenticatedError,
    ResolutionReasonRequiredError,
    TicketNotAssignedError,
    TicketNotFoundError,
    TicketStateConflictError,
    UnknownIntentError,
)
from safe_reply.models import Ticket, TicketCorrection
from safe_reply.schemas.agent import (
    AgentTicket,
    AgentTicketDetail,
    CorrectionCreate,
    CorrectionRead,
    ResolveRequest,
)

router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={
        error.status_code: {"description": error.detail}
        for error in (NotAuthenticatedError, ForbiddenError)
    },
)


def _responses(*errors: type[AppError]) -> dict[int | str, dict[str, Any]]:
    return {e.status_code: {"description": e.detail} for e in errors}


# Las acciones sobre un ticket: solo el agente que lo tiene asignado.
ACTION_ERRORS = _responses(
    TicketNotFoundError, TicketNotAssignedError, TicketStateConflictError
)


@router.get("/queue", response_model=list[AgentTicket])
async def read_queue(
    _: AgentUser, service: HumanQueueServiceDep, pagination: Pagination
) -> Sequence[Ticket]:
    """La cola humana por plazo. Solo para mirar: se atiende con /queue/claim."""
    return await service.queue(limit=pagination.limit, offset=pagination.offset)


@router.post(
    "/queue/claim",
    response_model=AgentTicketDetail,
    responses={status.HTTP_204_NO_CONTENT: {"description": "La cola está vacía"}},
)
async def claim_next_ticket(
    agent: AgentUser, service: HumanQueueServiceDep
) -> AgentTicketDetail | Response:
    """Asigna al agente el siguiente ticket (el de plazo más cercano)."""
    ticket = await service.claim_next(agent)
    if ticket is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return await service.detail(ticket)


@router.get("/tickets", response_model=list[AgentTicket])
async def list_my_tickets(
    agent: AgentUser, service: HumanQueueServiceDep, pagination: Pagination
) -> Sequence[Ticket]:
    """Los tickets que el agente ha reclamado y aún no ha resuelto."""
    return await service.my_tickets(
        agent, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/tickets/{ticket_id}", responses=_responses(TicketNotFoundError))
async def read_ticket_as_agent(
    ticket_id: uuid.UUID, _: AgentUser, service: HumanQueueServiceDep
) -> AgentTicketDetail:
    return await service.detail(await service.get(ticket_id))


@router.post(
    "/tickets/{ticket_id}/contacted",
    response_model=AgentTicket,
    responses=ACTION_ERRORS,
)
async def mark_contacted(
    ticket_id: uuid.UUID, agent: AgentUser, service: HumanQueueServiceDep
) -> Ticket:
    return await service.mark_contacted(agent, ticket_id)


@router.post(
    "/tickets/{ticket_id}/call-failed",
    response_model=AgentTicket,
    responses=ACTION_ERRORS,
)
async def record_failed_call(
    ticket_id: uuid.UUID, agent: AgentUser, service: HumanQueueServiceDep
) -> Ticket:
    """Llamada sin respuesta. Tras las del YAML, el ticket vuelve a la cola sin asignar."""
    return await service.record_failed_call(agent, ticket_id)


@router.post(
    "/tickets/{ticket_id}/resolve",
    response_model=AgentTicket,
    responses={**ACTION_ERRORS, **_responses(ResolutionReasonRequiredError)},
)
async def resolve_ticket(
    ticket_id: uuid.UUID,
    agent: AgentUser,
    service: HumanQueueServiceDep,
    data: ResolveRequest | None = None,
) -> Ticket:
    """Motivo obligatorio si no se llegó a contactar con el cliente."""
    reason = data.reason if data is not None else None
    return await service.resolve(agent, ticket_id, reason)


@router.post(
    "/tickets/{ticket_id}/corrections",
    status_code=status.HTTP_201_CREATED,
    response_model=CorrectionRead,
    responses=_responses(TicketNotFoundError, UnknownIntentError),
)
async def correct_ticket(
    ticket_id: uuid.UUID,
    data: CorrectionCreate,
    agent: AgentUser,
    service: HumanQueueServiceDep,
) -> TicketCorrection:
    return await service.correct(agent, ticket_id, data)
