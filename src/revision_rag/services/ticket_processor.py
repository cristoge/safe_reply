import logging
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
from revision_rag.triage import router
from revision_rag.triage.answers import CALL_TEMPLATE, CannedAnswers
from revision_rag.triage.router import Destination, decide_route, summarize
from revision_rag.triage.rules import PRIORITY_LEVELS, Rules
from revision_rag.triage.urgency import due_at, final_priority

logger = logging.getLogger(__name__)

# route_reason de los caminos que no salen del router.
CANNED_RAG_DISABLED = "canned answer (RAG not enabled yet)"
PROCESSING_ERROR = "processing error"
# Si el procesado falla no se sabe qué es: podría ser un fraude. Mejor un
# "hello" con prioridad alta que un fraude esperando 4 h (apartado 3.5).
PROCESSING_ERROR_PRIORITY = PRIORITY_LEVELS["high"]


class TicketProcessor:
    """Clasificar → reglas → urgencia → router → respuesta, para un ticket ya guardado.

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
            logger.exception("Error procesando el ticket %s", ticket_id)
            await self.session.rollback()
            await self._send_to_human_after_error(ticket_id)

    async def _decide_and_answer(self, ticket: Ticket) -> None:
        prediction = summarize(self.classifier.predict(ticket.text), self.rules)
        decision = decide_route(prediction, ticket.text, self.rules)
        rule = self.rules.intents[prediction.intent]

        window = timedelta(hours=self.rules.urgency.repeat_window_hours)
        recent = await self.tickets.count_recent_for_customer(
            ticket.customer_id, since=ticket.created_at - window
        )
        priority = final_priority(rule.priority, ticket.text, recent, self.rules)

        ticket.intent = prediction.intent
        ticket.confidence = prediction.confidence
        ticket.ticket_type = rule.ticket_type
        ticket.priority = priority.level
        ticket.priority_reason = priority.reason
        ticket.classifier_version = self.classifier.version

        match decision.destination:
            case Destination.HUMAN:
                reason = decision.reason
                assert reason is not None  # todas las reglas de persona dan motivo
                if reason in (router.ALWAYS_HUMAN, router.ALWAYS_HUMAN_LOW_CONFIDENCE):
                    # El agente ve también por qué esa intención va siempre a
                    # persona ("possible fraud").
                    reason = f"{reason}: {rule.reason}"
                self._to_human(ticket, reason)
                content = CALL_TEMPLATE
            case Destination.CANNED:
                self._to_ai(ticket, router.CANNED_FAQ)
                content = self.answers.for_intent(prediction.intent)
            case Destination.RAG:
                if self.rules.rag_enabled:
                    raise NotImplementedError("el RAG llega en la v2")
                # Mientras no haya RAG, las intenciones de problemas responden
                # con su predefinida (genérica a propósito, S6).
                self._to_ai(ticket, CANNED_RAG_DISABLED)
                content = self.answers.for_intent(prediction.intent)

        await self._finish(ticket, content)

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
