"""Lo que ve el cliente de sus tickets.

Sin intent, confidence, priority ni route_reason: son internos (los verá el
agente en la cola, S5). Una "priority: low" a un cliente enfadado no ayuda.
"""

import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from safe_reply.models.enums import MessageAuthor, TicketStatus
from safe_reply.models.ticket import TICKET_TEXT_MAX_LENGTH

# strip_whitespace antes de medir: "   " cuenta como vacío. El mismo límite que
# los CHECK de tickets.text y de lo que escribe el cliente en ticket_messages,
# para que reciba un 422 claro y no un 500.
CustomerText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=TICKET_TEXT_MAX_LENGTH
    ),
]


class TicketCreate(BaseModel):
    text: CustomerText


class MessageCreate(BaseModel):
    """El cliente contesta en un ticket ya respondido o que está con una persona."""

    text: CustomerText


class _TicketBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: TicketStatus
    # El capítulo ("cards_ordering_and_activation") da contexto al cliente;
    # null hasta que se procesa.
    ticket_type: str | None
    created_at: datetime
    updated_at: datetime


class TicketSummary(_TicketBase):
    """Un ticket en la lista de "mis tickets" (y la respuesta de POST /tickets)."""

    text: str


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    author: MessageAuthor
    content: str
    created_at: datetime


class TicketDetail(_TicketBase):
    """Un ticket con su conversación, para pintarla como un chat.

    El primer mensaje es tickets.text (no se guarda dos veces); después, los
    mensajes enviados de ticket_messages. Los borradores de la IA no salen nunca.
    """

    messages: list[MessageRead]
