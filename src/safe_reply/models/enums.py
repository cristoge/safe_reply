from enum import StrEnum

from sqlalchemy import Enum


class UserRole(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"


class TicketStatus(StrEnum):
    # Camino de la IA: received → processing → answered.
    # Camino de persona: … → in_human_queue → contacted → resolved.
    RECEIVED = "received"
    PROCESSING = "processing"
    ANSWERED = "answered"
    IN_HUMAN_QUEUE = "in_human_queue"
    CONTACTED = "contacted"
    RESOLVED = "resolved"


class Route(StrEnum):
    AI = "ai"
    HUMAN = "human"


class MessageAuthor(StrEnum):
    CUSTOMER = "customer"
    # Plantillas y respuestas predefinidas: textos fijos, no generados.
    SYSTEM = "system"
    AI = "ai"
    AGENT = "agent"


class MessageStatus(StrEnum):
    # draft = borrador de la IA que el juez no ha dejado enviar: lo ve el agente,
    # nunca el cliente.
    DRAFT = "draft"
    SENT = "sent"


class TicketEventType(StrEnum):
    CREATED = "created"
    STATUS_CHANGED = "status_changed"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    CALL_FAILED = "call_failed"
    ESCALATED = "escalated"
    # El cliente escribe en un ticket que ya está con una persona: no cambia el
    # estado, pero tiene que quedar en el historial.
    CUSTOMER_REPLIED = "customer_replied"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Columna varchar + CHECK (no un tipo ENUM nativo de Postgres).

    Así añadir o quitar valores es una migración normal. Se guardan los valores
    ("pending"), no los nombres ("PENDING"), y el CHECK hay que pedirlo
    explícitamente: en SQLAlchemy 2 no se crea por defecto.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(len(member.value) for member in enum_cls),
        values_callable=lambda cls: [member.value for member in cls],
    )
