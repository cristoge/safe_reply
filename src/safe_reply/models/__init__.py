# Importar aquí todos los modelos: Alembic los descubre a través de este módulo.
from safe_reply.models.refresh_token import RefreshToken
from safe_reply.models.ticket import Ticket
from safe_reply.models.ticket_correction import TicketCorrection
from safe_reply.models.ticket_event import TicketEvent
from safe_reply.models.ticket_message import TicketMessage
from safe_reply.models.user import User

__all__ = [
    "RefreshToken",
    "Ticket",
    "TicketCorrection",
    "TicketEvent",
    "TicketMessage",
    "User",
]
