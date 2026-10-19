import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import (
    ResolutionReasonRequiredError,
    TicketNotAssignedError,
    TicketNotFoundError,
    TicketStateConflictError,
    UnknownIntentError,
)
from revision_rag.models import (
    Ticket,
    TicketCorrection,
    TicketEvent,
    TicketMessage,
    User,
)
from revision_rag.models.enums import MessageAuthor, TicketEventType, TicketStatus
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_correction import TicketCorrectionRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.agent import (
    AgentMessageRead,
    AgentTicket,
    AgentTicketDetail,
    CorrectionCreate,
    CorrectionRead,
    CustomerContact,
    EventRead,
)
from revision_rag.triage.answers import unreachable_message
from revision_rag.triage.rules import Rules

logger = logging.getLogger(__name__)

# reason del unassigned tras las llamadas fallidas del YAML. Fijo (sin el número
# de llamadas): es lo que busca count_failed_calls para empezar la cuenta de cero.
CUSTOMER_UNREACHABLE = "customer unreachable"
NO_ANSWER = "no answer"


class HumanQueueService:
    """Lo que hace el agente: reclamar el siguiente ticket, llamar, resolver y corregir.

    El agente no elige ticket: solo "dame el siguiente" (por plazo). Si pudiera
    elegir, se quedaría con los fáciles y los difíciles esperarían. Las
    acciones sobre un ticket bloquean su fila (FOR UPDATE) y solo las puede
    hacer el agente que lo tiene asignado.
    """

    def __init__(
        self,
        session: AsyncSession,
        tickets: TicketRepository,
        messages: TicketMessageRepository,
        events: TicketEventRepository,
        corrections: TicketCorrectionRepository,
        users: UserRepository,
        rules: Rules,
    ) -> None:
        self.session = session
        self.tickets = tickets
        self.messages = messages
        self.events = events
        self.corrections = corrections
        self.users = users
        self.rules = rules

    async def queue(self, *, limit: int, offset: int) -> Sequence[Ticket]:
        return await self.tickets.list_queue(limit=limit, offset=offset)

    async def my_tickets(
        self, agent: User, *, limit: int, offset: int
    ) -> Sequence[Ticket]:
        return await self.tickets.list_open_for_agent(
            agent.id, limit=limit, offset=offset
        )

    async def claim_next(self, agent: User) -> Ticket | None:
        """Asigna al agente el siguiente ticket de la cola. None si no queda ninguno."""
        now = datetime.now(UTC)
        timeout = self.rules.human_queue.claim_timeout_hours
        ticket = await self.tickets.claim_next(
            stale_before=now - timedelta(hours=timeout)
        )
        if ticket is None:
            logger.info("queue_empty", extra={"agent_id": agent.id})
            return None
        reclaimed = ticket.assigned_agent_id is not None
        if reclaimed:
            # Lo reclamó otro hace más del plazo y no llegó a contactar: el
            # historial dice por qué cambió de manos.
            await self._event(
                ticket,
                TicketEventType.UNASSIGNED,
                reason=f"claimed > {timeout} h without contact",
            )
        ticket.assigned_agent_id = agent.id
        ticket.assigned_at = now
        await self._event(ticket, TicketEventType.ASSIGNED, actor=agent)
        await self.session.commit()
        assert ticket.due_at is not None  # todo ticket en la cola tiene plazo
        logger.info(
            "ticket_claimed",
            extra={
                "ticket_id": ticket.id,
                "agent_id": agent.id,
                "reclaimed": reclaimed,
                "priority": ticket.priority,
                # Lo que esperó el cliente y si se pasó del plazo: las dos
                # cifras que dicen si el equipo da abasto.
                "waited_s": round((now - ticket.created_at).total_seconds()),
                "overdue": now > ticket.due_at,
            },
        )
        return ticket

    async def get(self, ticket_id: uuid.UUID) -> Ticket:
        # Sin filtro de dueño: los agentes atienden los tickets de todos.
        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            raise TicketNotFoundError
        return ticket

    async def detail(self, ticket: Ticket) -> AgentTicketDetail:
        customer = await self.users.get_by_id(ticket.customer_id)
        assert customer is not None  # CASCADE: sin cliente no hay ticket
        messages = await self.messages.list_all(ticket.id)
        events = await self.events.list_for_ticket(ticket.id)
        corrections = await self.corrections.list_for_ticket(ticket.id)
        return AgentTicketDetail(
            **AgentTicket.model_validate(ticket).model_dump(),
            customer=CustomerContact.model_validate(customer),
            messages=[AgentMessageRead.model_validate(m) for m in messages],
            events=[EventRead.model_validate(e) for e in events],
            corrections=[CorrectionRead.model_validate(c) for c in corrections],
        )

    async def mark_contacted(self, agent: User, ticket_id: uuid.UUID) -> Ticket:
        """in_human_queue → contacted: el agente ha hablado con el cliente."""
        ticket = await self._lock_assigned(agent, ticket_id)
        match ticket.status:
            case TicketStatus.CONTACTED:
                return ticket  # un reintento: ya estaba
            case TicketStatus.IN_HUMAN_QUEUE:
                await self._change_status(ticket, TicketStatus.CONTACTED, agent)
            case _:
                raise TicketStateConflictError("El ticket ya está resuelto")
        await self.session.commit()
        logger.info(
            "ticket_contacted", extra={"ticket_id": ticket.id, "agent_id": agent.id}
        )
        return ticket

    async def record_failed_call(self, agent: User, ticket_id: uuid.UUID) -> Ticket:
        """Una llamada sin respuesta. Tras las del YAML, aviso al cliente y a la cola.

        Vuelve sin asignar (con el mismo plazo) para que lo intente otro agente
        o para cuando el cliente conteste con una hora a la que llamarle.
        """
        ticket = await self._lock_assigned(agent, ticket_id)
        if ticket.status != TicketStatus.IN_HUMAN_QUEUE:
            raise TicketStateConflictError(
                "Solo se registran llamadas fallidas antes de contactar con el cliente"
            )
        await self._event(
            ticket, TicketEventType.CALL_FAILED, actor=agent, reason=NO_ANSWER
        )
        max_calls = self.rules.human_queue.max_failed_calls
        failed = await self.events.count_failed_calls(
            ticket.id, reset_reason=CUSTOMER_UNREACHABLE
        )
        if failed >= max_calls:
            await self.messages.add(
                TicketMessage(
                    ticket_id=ticket.id,
                    author=MessageAuthor.SYSTEM,
                    content=unreachable_message(max_calls, ticket.ticket_type),
                )
            )
            ticket.assigned_agent_id = None
            ticket.assigned_at = None
            # actor null: lo devuelve a la cola la regla del YAML, no el agente.
            await self._event(
                ticket, TicketEventType.UNASSIGNED, reason=CUSTOMER_UNREACHABLE
            )
        await self.session.commit()
        logger.info(
            "call_failed",
            extra={
                "ticket_id": ticket.id,
                "agent_id": agent.id,
                "failed_calls": failed,
                "unreachable": failed >= max_calls,
            },
        )
        return ticket

    async def resolve(
        self, agent: User, ticket_id: uuid.UUID, reason: str | None
    ) -> Ticket:
        """→ resolved. Sin haber contactado se puede, pero con motivo obligatorio.

        Si no, un "hello" o un spam obligarían a llamar para poder cerrarlos.
        """
        ticket = await self._lock_assigned(agent, ticket_id)
        match ticket.status:
            case TicketStatus.RESOLVED:
                return ticket  # un reintento: ya estaba
            case TicketStatus.IN_HUMAN_QUEUE if reason is None:
                raise ResolutionReasonRequiredError
            case _:
                contacted = ticket.status == TicketStatus.CONTACTED
                await self._change_status(
                    ticket, TicketStatus.RESOLVED, agent, reason=reason
                )
        await self.session.commit()
        # Sin el motivo: lo escribe el agente a mano (puede llevar datos del cliente).
        logger.info(
            "ticket_resolved",
            extra={
                "ticket_id": ticket.id,
                "agent_id": agent.id,
                "contacted": contacted,
            },
        )
        return ticket

    async def correct(
        self, agent: User, ticket_id: uuid.UUID, data: CorrectionCreate
    ) -> TicketCorrection:
        """Guarda la revisión del agente (cualquier agente, en cualquier estado).

        Los predicted_* se copian del ticket ahora: si después se reprocesa con
        otro modelo, la corrección sigue diciendo qué se corrigió.
        """
        ticket = await self.get(ticket_id)
        # Una intención que el modelo no conoce no sirve para reentrenar.
        if (
            data.corrected_intent is not None
            and data.corrected_intent not in self.rules.intents
        ):
            raise UnknownIntentError
        correction = await self.corrections.add(
            TicketCorrection(
                ticket_id=ticket.id,
                agent_id=agent.id,
                predicted_intent=ticket.intent,
                corrected_intent=data.corrected_intent,
                predicted_priority=ticket.priority,
                corrected_priority=data.corrected_priority,
                predicted_route=ticket.route,
                corrected_route=data.corrected_route,
                note=data.note,
            )
        )
        await self.session.commit()
        # Sin la nota (texto libre). Qué se corrigió, para ver qué falla más.
        logger.info(
            "ticket_corrected",
            extra={
                "ticket_id": ticket.id,
                "agent_id": agent.id,
                "fields": sorted(
                    f
                    for f in ("intent", "priority", "route")
                    if getattr(data, f"corrected_{f}") is not None
                ),
            },
        )
        return correction

    async def _lock_assigned(self, agent: User, ticket_id: uuid.UUID) -> Ticket:
        """El ticket con la fila bloqueada, si está en el camino de persona y es del agente."""
        ticket = await self.tickets.get_for_update(ticket_id)
        if ticket is None:
            raise TicketNotFoundError
        if ticket.status not in (
            TicketStatus.IN_HUMAN_QUEUE,
            TicketStatus.CONTACTED,
            TicketStatus.RESOLVED,
        ):
            raise TicketStateConflictError("El ticket no está en la cola humana")
        # Tras un reclamado y abandonado, el primer agente ya no lo tiene: si
        # siguiera actuando, el cliente recibiría dos llamadas.
        if ticket.assigned_agent_id != agent.id:
            raise TicketNotAssignedError
        return ticket

    async def _change_status(
        self,
        ticket: Ticket,
        to_status: TicketStatus,
        agent: User,
        reason: str | None = None,
    ) -> None:
        await self.events.add(
            TicketEvent(
                ticket_id=ticket.id,
                event=TicketEventType.STATUS_CHANGED,
                from_status=ticket.status,
                to_status=to_status,
                actor_id=agent.id,
                reason=reason,
            )
        )
        ticket.status = to_status

    async def _event(
        self,
        ticket: Ticket,
        event: TicketEventType,
        *,
        actor: User | None = None,
        reason: str | None = None,
    ) -> None:
        await self.events.add(
            TicketEvent(
                ticket_id=ticket.id,
                event=event,
                actor_id=actor.id if actor is not None else None,
                reason=reason,
            )
        )
