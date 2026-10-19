"""HumanQueueService contra Postgres real: reclamar, llamar, resolver y corregir."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import (
    ResolutionReasonRequiredError,
    TicketNotAssignedError,
    TicketStateConflictError,
    UnknownIntentError,
)
from revision_rag.models import Ticket, TicketEvent, TicketMessage, User
from revision_rag.models.enums import (
    MessageAuthor,
    Route,
    TicketEventType,
    TicketStatus,
    UserRole,
)
from revision_rag.repositories.ticket import TicketRepository
from revision_rag.repositories.ticket_correction import TicketCorrectionRepository
from revision_rag.repositories.ticket_event import TicketEventRepository
from revision_rag.repositories.ticket_message import TicketMessageRepository
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.agent import CorrectionCreate
from revision_rag.services.human_queue import (
    CUSTOMER_UNREACHABLE,
    HumanQueueService,
)
from revision_rag.triage.answers import unreachable_message
from revision_rag.triage.rules import Rules


@pytest.fixture
def service(db_session: AsyncSession, rules: Rules) -> HumanQueueService:
    return HumanQueueService(
        db_session,
        TicketRepository(db_session),
        TicketMessageRepository(db_session),
        TicketEventRepository(db_session),
        TicketCorrectionRepository(db_session),
        UserRepository(db_session),
        rules,
    )


async def new_user(db_session: AsyncSession, email: str, role: UserRole) -> User:
    user = User(email=email, hashed_password="x", role=role, phone="+34600123456")
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
async def customer(db_session: AsyncSession) -> User:
    return await new_user(db_session, "cristo@test.com", UserRole.CUSTOMER)


@pytest.fixture
async def agent(db_session: AsyncSession) -> User:
    return await new_user(db_session, "ana@banco.com", UserRole.AGENT)


@pytest.fixture
async def other_agent(db_session: AsyncSession) -> User:
    return await new_user(db_session, "luis@banco.com", UserRole.AGENT)


async def queued_ticket(
    db_session: AsyncSession,
    customer: User,
    *,
    due_in_hours: float = 1,
    ticket_type: str = "transfers",
    **fields: object,
) -> Ticket:
    """Un ticket ya procesado que espera en la cola humana."""
    values: dict[str, object] = {
        "customer_id": customer.id,
        "text": "I sent money to my brother and he hasn't got it",
        "status": TicketStatus.IN_HUMAN_QUEUE,
        "intent": "transfer_not_received_by_recipient",
        "confidence": 0.91,
        "ticket_type": ticket_type,
        "priority": 3,
        "route": Route.HUMAN,
        "route_reason": "intent always human: possible lost money",
        "due_at": datetime.now(UTC) + timedelta(hours=due_in_hours),
    }
    ticket = Ticket(**(values | fields))
    db_session.add(ticket)
    await db_session.commit()
    return ticket


async def events_of(
    db_session: AsyncSession, ticket: Ticket
) -> list[tuple[str, object, str | None]]:
    rows = await db_session.scalars(
        select(TicketEvent)
        .where(TicketEvent.ticket_id == ticket.id)
        .order_by(TicketEvent.created_at, TicketEvent.id)
    )
    return [(e.event, e.actor_id, e.reason) for e in rows]


# --- Reclamar -----------------------------------------------------------------


async def test_claim_takes_the_closest_due_date_first(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    # Un low de hace casi 24 h va antes que un high recién llegado (sin inanición).
    later = await queued_ticket(db_session, customer, due_in_hours=1, priority=3)
    sooner = await queued_ticket(db_session, customer, due_in_hours=0.5, priority=1)

    first = await service.claim_next(agent)
    second = await service.claim_next(agent)

    assert first is not None and second is not None
    assert [first.id, second.id] == [sooner.id, later.id]
    assert first.assigned_agent_id == agent.id
    assert first.assigned_at is not None
    assert await events_of(db_session, first) == [
        (TicketEventType.ASSIGNED, agent.id, None)
    ]


async def test_claim_skips_claimed_and_non_queued_tickets(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    other_agent: User,
) -> None:
    await queued_ticket(
        db_session,
        customer,
        assigned_agent_id=other_agent.id,
        assigned_at=datetime.now(UTC),
    )
    await queued_ticket(db_session, customer, status=TicketStatus.CONTACTED)
    await queued_ticket(db_session, customer, status=TicketStatus.ANSWERED)

    assert await service.claim_next(agent) is None


async def test_claimed_and_abandoned_ticket_can_be_claimed_again(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    other_agent: User,
    rules: Rules,
) -> None:
    hours = rules.human_queue.claim_timeout_hours
    abandoned = await queued_ticket(
        db_session,
        customer,
        assigned_agent_id=other_agent.id,
        assigned_at=datetime.now(UTC) - timedelta(hours=hours, minutes=1),
    )

    claimed = await service.claim_next(agent)

    assert claimed is not None and claimed.id == abandoned.id
    assert claimed.assigned_agent_id == agent.id
    # El historial dice por qué cambió de manos (lo hizo el sistema: actor None).
    assert await events_of(db_session, claimed) == [
        (TicketEventType.UNASSIGNED, None, f"claimed > {hours} h without contact"),
        (TicketEventType.ASSIGNED, agent.id, None),
    ]
    # El primer agente ya no puede actuar: el cliente recibiría dos llamadas.
    with pytest.raises(TicketNotAssignedError):
        await service.mark_contacted(other_agent, abandoned.id)


# --- Llamadas -----------------------------------------------------------------


async def claimed_ticket(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    ticket_type: str = "transfers",
) -> Ticket:
    await queued_ticket(db_session, customer, ticket_type=ticket_type)
    ticket = await service.claim_next(agent)
    assert ticket is not None
    return ticket


async def test_contacted_then_resolved(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    ticket = await claimed_ticket(service, db_session, customer, agent)

    await service.mark_contacted(agent, ticket.id)
    # Un reintento no hace nada (ni un segundo evento).
    await service.mark_contacted(agent, ticket.id)
    await service.resolve(agent, ticket.id, reason=None)
    await service.resolve(agent, ticket.id, reason=None)

    assert ticket.status == TicketStatus.RESOLVED
    rows = await db_session.scalars(
        select(TicketEvent)
        .where(
            TicketEvent.ticket_id == ticket.id,
            TicketEvent.event == TicketEventType.STATUS_CHANGED,
        )
        .order_by(TicketEvent.created_at, TicketEvent.id)
    )
    assert [(e.from_status, e.to_status, e.actor_id) for e in rows] == [
        ("in_human_queue", "contacted", agent.id),
        ("contacted", "resolved", agent.id),
    ]


async def test_resolving_without_contact_needs_a_reason(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    ticket = await claimed_ticket(service, db_session, customer, agent)

    with pytest.raises(ResolutionReasonRequiredError):
        await service.resolve(agent, ticket.id, reason=None)
    await service.resolve(agent, ticket.id, reason="spam")

    assert ticket.status == TicketStatus.RESOLVED
    assert (await events_of(db_session, ticket))[-1] == (
        TicketEventType.STATUS_CHANGED,
        agent.id,
        "spam",
    )


async def test_only_the_assigned_agent_can_act(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    other_agent: User,
) -> None:
    ticket = await claimed_ticket(service, db_session, customer, agent)

    with pytest.raises(TicketNotAssignedError):
        await service.mark_contacted(other_agent, ticket.id)
    with pytest.raises(TicketNotAssignedError):
        await service.record_failed_call(other_agent, ticket.id)
    with pytest.raises(TicketNotAssignedError):
        await service.resolve(other_agent, ticket.id, reason="spam")


async def test_actions_on_a_ticket_outside_the_human_queue_conflict(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    answered = await queued_ticket(db_session, customer, status=TicketStatus.ANSWERED)

    with pytest.raises(TicketStateConflictError):
        await service.mark_contacted(agent, answered.id)


async def test_failed_call_after_contact_conflicts(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    ticket = await claimed_ticket(service, db_session, customer, agent)
    await service.mark_contacted(agent, ticket.id)

    with pytest.raises(TicketStateConflictError):
        await service.record_failed_call(agent, ticket.id)


async def system_messages(db_session: AsyncSession, ticket: Ticket) -> list[str]:
    rows = await db_session.scalars(
        select(TicketMessage.content)
        .where(
            TicketMessage.ticket_id == ticket.id,
            TicketMessage.author == MessageAuthor.SYSTEM,
        )
        .order_by(TicketMessage.created_at, TicketMessage.id)
    )
    return list(rows)


async def test_after_the_max_failed_calls_the_customer_is_told_and_it_goes_back(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    rules: Rules,
) -> None:
    max_calls = rules.human_queue.max_failed_calls
    ticket = await claimed_ticket(service, db_session, customer, agent)

    for _ in range(max_calls - 1):
        await service.record_failed_call(agent, ticket.id)
    assert ticket.assigned_agent_id == agent.id  # aún le quedan intentos
    assert await system_messages(db_session, ticket) == []

    await service.record_failed_call(agent, ticket.id)

    # Vuelve a la cola sin asignar, con el aviso al cliente.
    assert ticket.status == TicketStatus.IN_HUMAN_QUEUE
    assert (ticket.assigned_agent_id, ticket.assigned_at) == (None, None)
    assert await system_messages(db_session, ticket) == [
        unreachable_message(max_calls, "transfers")
    ]
    assert (await events_of(db_session, ticket))[-2:] == [
        (TicketEventType.CALL_FAILED, agent.id, "no answer"),
        (TicketEventType.UNASSIGNED, None, CUSTOMER_UNREACHABLE),
    ]


async def test_fraud_tickets_also_say_to_freeze_the_card(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    rules: Rules,
) -> None:
    ticket = await claimed_ticket(
        service, db_session, customer, agent, ticket_type="security_and_fraud"
    )

    for _ in range(rules.human_queue.max_failed_calls):
        await service.record_failed_call(agent, ticket.id)

    [message] = await system_messages(db_session, ticket)
    assert "Cards › Freeze card" in message


async def test_failed_calls_count_from_zero_after_the_notice(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    other_agent: User,
    rules: Rules,
) -> None:
    max_calls = rules.human_queue.max_failed_calls
    ticket = await claimed_ticket(service, db_session, customer, agent)
    for _ in range(max_calls):
        await service.record_failed_call(agent, ticket.id)

    # Otro agente lo reclama: con una llamada fallida no se vuelve a avisar.
    assert (await service.claim_next(other_agent)) is not None
    await service.record_failed_call(other_agent, ticket.id)

    assert ticket.assigned_agent_id == other_agent.id
    assert len(await system_messages(db_session, ticket)) == 1


async def test_an_abandoned_claim_does_not_reset_the_failed_calls(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
    other_agent: User,
    rules: Rules,
) -> None:
    max_calls = rules.human_queue.max_failed_calls
    ticket = await claimed_ticket(service, db_session, customer, agent)
    for _ in range(max_calls - 1):
        await service.record_failed_call(agent, ticket.id)
    # El primer agente lo abandona: pasado el plazo, lo reclama otro.
    ticket.assigned_at = datetime.now(UTC) - timedelta(
        hours=rules.human_queue.claim_timeout_hours + 1
    )
    await db_session.commit()
    assert (await service.claim_next(other_agent)) is not None

    # Sigue la cuenta del anterior: el cliente no recibe más llamadas de la cuenta.
    await service.record_failed_call(other_agent, ticket.id)

    assert ticket.assigned_agent_id is None
    assert len(await system_messages(db_session, ticket)) == 1


# --- Correcciones -------------------------------------------------------------


async def test_correction_copies_what_the_system_predicted(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    ticket = await queued_ticket(db_session, customer)

    correction = await service.correct(
        agent,
        ticket.id,
        CorrectionCreate(corrected_intent="pending_transfer", corrected_priority=2),
    )

    assert (correction.predicted_intent, correction.corrected_intent) == (
        "transfer_not_received_by_recipient",
        "pending_transfer",
    )
    assert (correction.predicted_priority, correction.corrected_priority) == (3, 2)
    # La ruta no se corrige: null = de acuerdo.
    assert (correction.predicted_route, correction.corrected_route) == (
        Route.HUMAN,
        None,
    )
    assert correction.agent_id == agent.id


async def test_correction_with_an_unknown_intent_is_rejected(
    service: HumanQueueService,
    db_session: AsyncSession,
    customer: User,
    agent: User,
) -> None:
    ticket = await queued_ticket(db_session, customer)

    with pytest.raises(UnknownIntentError):
        await service.correct(
            agent, ticket.id, CorrectionCreate(corrected_intent="card_stuck_in_atm")
        )
