import logging
import time
import uuid
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.clients.classifier import IntentClassifier
from revision_rag.models import Ticket, TicketEvent, TicketMessage
from revision_rag.models.enums import (
    MessageAuthor,
    Route,
    TicketEventType,
    TicketStatus,
)
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.triage.answers import CALL_TEMPLATE, CannedAnswers
from revision_rag.triage.pipeline import triage_ticket
from revision_rag.triage.rules import PRIORITY_LEVELS, Rules
from revision_rag.triage.urgency import due_at

logger = logging.getLogger(__name__)

# route_reason del camino de error (el resto los da triage_ticket).
PROCESSING_ERROR = "processing error"
# Si el procesado falla no se sabe qué es: podría ser un fraude. Mejor un
# "hello" con prioridad alta que un fraude esperando 4 h (apartado 3.5).
PROCESSING_ERROR_PRIORITY = PRIORITY_LEVELS["high"]


class TicketProcessor:
    """Clasificar → triage_ticket → guardar, para un ticket ya guardado.

    La misma lógica en la v1 (InlineTaskQueue, en la propia petición) y en la v2
    (un worker de ARQ): solo cambia quién llama a process_ticket.
    """

    def __init__(
        self,
        session: AsyncSession,
        tickets: TicketRepository,
        messages: TicketMessageRepository,
        events: TicketEventRepository,
        classifier: IntentClassifier,
        rules: Rules,
        answers: CannedAnswers,
    ) -> None:
        self.session = session
        self.tickets = tickets
        self.messages = messages
        self.events = events
        self.classifier = classifier
        self.rules = rules
        self.answers = answers

    async def process_ticket(self, ticket_id: uuid.UUID) -> None:
        ticket = await self.tickets.start_processing(ticket_id)
        if ticket is None:
            # Ya lo está procesando otro, o ya está procesado: no hacer nada.
            return
        await self._status_event(
            ticket_id, TicketStatus.RECEIVED, TicketStatus.PROCESSING
        )
        # Se confirma "processing" antes de trabajar: con un worker (v2), un
        # ticket que se queda aquí es uno que murió a mitad, y el barrido lo ve.
        await self.session.commit()

        try:
            await self._decide_and_answer(ticket)
        except Exception:
            # Cualquier fallo (el modelo, un bug) → persona. El estado nunca se
            # queda atascado y el cliente recibe igualmente la plantilla.
            logger.exception("ticket_processing_failed", extra={"ticket_id": ticket_id})
            await self.session.rollback()
            await self._send_to_human_after_error(ticket_id)

    async def _decide_and_answer(self, ticket: Ticket) -> None:
        start = time.perf_counter()
        window = timedelta(hours=self.rules.urgency.repeat_window_hours)
        recent = await self.tickets.count_recent_for_customer(
            ticket.customer_id, since=ticket.created_at - window
        )
        result = triage_ticket(
            ticket.text,
            self.classifier.predict(ticket.text),
            recent,
            self.rules,
            self.answers,
        )

        ticket.intent = result.intent
        ticket.confidence = result.confidence
        ticket.ticket_type = result.ticket_type
        ticket.priority = result.priority.level
        ticket.priority_reason = result.priority.reason
        ticket.classifier_version = self.classifier.version
        if result.route == Route.HUMAN:
            self._to_human(ticket, result.route_reason)
        else:
            self._to_ai(ticket, result.route_reason)
        await self._finish(ticket, result.answer)
        # Después del commit: solo se registra lo que de verdad se guardó.
        logger.info(
            "ticket_triaged",
            extra={
                "ticket_id": ticket.id,
                "intent": result.intent,
                "confidence": round(result.confidence, 3),
                "ticket_type": result.ticket_type,
                "priority": result.priority.level,
                "route": result.route,
                "route_reason": result.route_reason,
                "classifier_version": self.classifier.version,
                "duration_ms": round((time.perf_counter() - start) * 1000, 1),
            },
        )

    async def _send_to_human_after_error(self, ticket_id: uuid.UUID) -> None:
        # Recargado: el rollback ha descartado lo que se hubiera cambiado a mitad.
        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return
        ticket.priority = PROCESSING_ERROR_PRIORITY
        ticket.priority_reason = "high by default (processing error)"
        self._to_human(ticket, PROCESSING_ERROR)
        await self._finish(ticket, CALL_TEMPLATE)

    def _to_human(self, ticket: Ticket, reason: str) -> None:
        ticket.route = Route.HUMAN
        ticket.route_reason = reason
        ticket.status = TicketStatus.IN_HUMAN_QUEUE
        # El plazo cuenta desde que el cliente escribió, no desde que se procesó.
        assert ticket.priority is not None
        ticket.due_at = due_at(ticket.created_at, ticket.priority, self.rules)

    @staticmethod
    def _to_ai(ticket: Ticket, reason: str) -> None:
        ticket.route = Route.AI
        ticket.route_reason = reason
        ticket.status = TicketStatus.ANSWERED

    async def _finish(self, ticket: Ticket, content: str) -> None:
        """La respuesta al cliente y el evento, en el mismo commit que el ticket."""
        await self.messages.add(
            TicketMessage(
                ticket_id=ticket.id, author=MessageAuthor.SYSTEM, content=content
            )
        )
        await self._status_event(
            ticket.id,
            TicketStatus.PROCESSING,
            ticket.status,
            reason=ticket.route_reason if ticket.route == Route.HUMAN else None,
        )
        await self.session.commit()

    async def _status_event(
        self,
        ticket_id: uuid.UUID,
        from_status: TicketStatus,
        to_status: TicketStatus,
        reason: str | None = None,
    ) -> None:
        # actor_id null: lo hace el sistema, no una persona.
        await self.events.add(
            TicketEvent(
                ticket_id=ticket_id,
                event=TicketEventType.STATUS_CHANGED,
                from_status=from_status,
                to_status=to_status,
                reason=reason,
            )
        )
