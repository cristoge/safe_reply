"""Lo que ve el agente: el ticket entero, con lo que el cliente no ve.

Clasificación con sus motivos, prioridad, plazo, borradores de la IA, el
historial de eventos y el teléfono del cliente (le va a llamar). Misma tabla
que la vista del cliente, otro schema.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from revision_rag.models.enums import (
    MessageAuthor,
    MessageStatus,
    Route,
    TicketEventType,
    TicketStatus,
)

Priority = Annotated[
    int, Field(ge=1, le=3, description="1 = low · 2 = medium · 3 = high")
]


class _FromOrm(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class AgentTicket(_FromOrm):
    """Un ticket en la cola o en "mis tickets", y la respuesta de las acciones."""

    id: uuid.UUID
    text: str
    status: TicketStatus
    intent: str | None
    confidence: float | None
    ticket_type: str | None
    priority: int | None
    priority_reason: str | None
    route: Route | None
    route_reason: str | None
    classifier_version: str | None
    escalated_at: datetime | None
    assigned_agent_id: uuid.UUID | None
    assigned_at: datetime | None
    due_at: datetime | None
    created_at: datetime
    updated_at: datetime


class CustomerContact(_FromOrm):
    email: str
    display_name: str | None
    phone: str | None


class AgentMessageRead(_FromOrm):
    id: uuid.UUID
    author: MessageAuthor
    agent_id: uuid.UUID | None
    content: str
    status: MessageStatus
    sources: list[dict[str, Any]] | None
    judge_passed: bool | None
    judge_reason: str | None
    model: str | None
    prompt_version: str | None
    created_at: datetime


class EventRead(_FromOrm):
    event: TicketEventType
    from_status: TicketStatus | None
    to_status: TicketStatus | None
    actor_id: uuid.UUID | None
    reason: str | None
    created_at: datetime


class CorrectionRead(_FromOrm):
    id: uuid.UUID
    agent_id: uuid.UUID | None
    predicted_intent: str | None
    corrected_intent: str | None
    predicted_priority: int | None
    corrected_priority: int | None
    predicted_route: Route | None
    corrected_route: Route | None
    note: str | None
    created_at: datetime


class AgentTicketDetail(AgentTicket):
    """El ticket con el cliente, la conversación entera y su historial.

    messages son las filas de ticket_messages (borradores incluidos); el primer
    mensaje del cliente es text.
    """

    customer: CustomerContact
    messages: list[AgentMessageRead]
    events: list[EventRead]
    corrections: list[CorrectionRead]


class ResolveRequest(BaseModel):
    # Obligatorio si no se llegó a contactar con el cliente ("spam", "not a
    # bank issue"...): lo comprueba el servicio, que es quien sabe el estado.
    reason: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ] = None


class CorrectionCreate(BaseModel):
    """Lo que el agente corrige; un campo sin dar (null) = de acuerdo con el sistema.

    Los predicted_* no se piden: se copian del ticket al guardar.
    """

    corrected_intent: Annotated[str | None, Field(max_length=100)] = None
    corrected_priority: Priority | None = None
    corrected_route: Route | None = None
    note: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1000),
    ] = None
