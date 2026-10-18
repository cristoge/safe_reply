# Importar aquí todos los modelos: Alembic los descubre a través de este módulo.
from revision_rag.models.refresh_token import RefreshToken
from revision_rag.models.ticket import Ticket
from revision_rag.models.ticket_correction import TicketCorrection
from revision_rag.models.ticket_event import TicketEvent
from revision_rag.models.ticket_message import TicketMessage
from revision_rag.models.user import User

__all__ = [
    "RefreshToken",
    "Ticket",
    "TicketCorrection",
    "TicketEvent",
    "TicketMessage",
    "User",
]
