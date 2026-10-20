from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, Response, status

from safe_reply.api.deps import (
    CustomerUser,
    OwnedTicket,
    Pagination,
    TicketServiceDep,
)
from safe_reply.core.exceptions import (
    ForbiddenError,
    IdempotencyKeyReusedError,
    NotAuthenticatedError,
    PhoneRequiredError,
    RateLimitExceededError,
    TicketNotFoundError,
    TicketStateConflictError,
)
from safe_reply.models import Ticket
from safe_reply.schemas.ticket import (
    MessageCreate,
    TicketCreate,
    TicketDetail,
    TicketSummary,
)

router = APIRouter(
    prefix="/tickets",
    tags=["tickets"],
    responses={
        error.status_code: {"description": error.detail}
        for error in (NotAuthenticatedError, ForbiddenError)
    },
)

NOT_FOUND: dict[int | str, dict[str, Any]] = {
    TicketNotFoundError.status_code: {"description": TicketNotFoundError.detail}
}
NOT_FOUND_OR_CONFLICT: dict[int | str, dict[str, Any]] = {
    **NOT_FOUND,
    TicketStateConflictError.status_code: {
        "description": "Resuelto (abre uno nuevo) o aún procesándose"
    },
}


@router.post(
    "",
    # 202 y no 201: el ticket se ha recibido, pero la respuesta llega después
    # (clasificar y enrutar). En la v1 se procesará en la misma petición; con
    # 202 desde ya, la API no cambia cuando llegue la cola (ARQ, v2).
    status_code=status.HTTP_202_ACCEPTED,
    response_model=TicketSummary,
    responses={
        RateLimitExceededError.status_code: {
            "description": RateLimitExceededError.detail
        },
        PhoneRequiredError.status_code: {"description": PhoneRequiredError.detail},
        IdempotencyKeyReusedError.status_code: {
            "description": f"{IdempotencyKeyReusedError.detail} (o datos no válidos)"
        },
    },
)
async def create_ticket(
    data: TicketCreate,
    customer: CustomerUser,
    service: TicketServiceDep,
    request: Request,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=255,
            description="Opcional (p. ej. un UUID por ticket). Reintentar con la "
            "misma key devuelve el mismo ticket en vez de crear otro.",
        ),
    ] = None,
) -> Ticket:
    ticket = await service.create(customer, data, idempotency_key)
    # Dónde consultar el estado del ticket: lo habitual con un 202. url_for
    # incluye el prefijo con el que se monta el router (/api/v1).
    response.headers["Location"] = str(
        request.url_for("read_ticket", ticket_id=ticket.id)
    )
    return ticket


@router.get("", response_model=list[TicketSummary])
async def list_tickets(
    customer: CustomerUser, service: TicketServiceDep, pagination: Pagination
) -> Sequence[Ticket]:
    return await service.list_tickets(
        customer, limit=pagination.limit, offset=pagination.offset
    )


@router.get("/{ticket_id}", responses=NOT_FOUND)
async def read_ticket(ticket: OwnedTicket, service: TicketServiceDep) -> TicketDetail:
    return await service.detail(ticket)


@router.post("/{ticket_id}/escalate", responses=NOT_FOUND_OR_CONFLICT)
async def escalate_ticket(
    ticket: OwnedTicket, service: TicketServiceDep
) -> TicketDetail:
    """ "I still need help": el ticket pasa a una persona, que llamará al cliente."""
    ticket = await service.escalate(ticket)
    return await service.detail(ticket)


@router.post(
    "/{ticket_id}/messages",
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND_OR_CONFLICT,
)
async def reply_to_ticket(
    data: MessageCreate, ticket: OwnedTicket, service: TicketServiceDep
) -> TicketDetail:
    """El cliente contesta. Tras una respuesta, es escalar con su texto."""
    ticket = await service.reply(ticket, data)
    return await service.detail(ticket)
