"""Las consultas de alerta contra Postgres, con tickets puestos a mano."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import Ticket, User
from revision_rag.models.enums import Route, TicketStatus
from revision_rag.repositories.ticket_stats import TicketStatsRepository
from revision_rag.services.alerts import AlertService
from revision_rag.triage.rules import Rules

NOW = datetime.now(UTC)


@pytest.fixture
async def customer(db_session: AsyncSession) -> User:
    user = User(email="cliente@test.com", hashed_password="x", phone="+34600123456")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
def service(db_session: AsyncSession, rules: Rules) -> AlertService:
    # Umbrales pequeños: con 30 tickets por caso el test no se leería.
    alerts = rules.alerts.model_copy(update={"min_tickets": 4})
    return AlertService(
        TicketStatsRepository(db_session), rules.model_copy(update={"alerts": alerts})
    )


async def add(
    db_session: AsyncSession, customer: User, n: int = 1, **fields: Any
) -> None:
    values = {
        "text": "x",
        "status": TicketStatus.ANSWERED,
        "route": Route.AI,
        "intent": "age_limit",
        "created_at": NOW - timedelta(hours=1),
    } | fields
    db_session.add_all(Ticket(customer_id=customer.id, **values) for _ in range(n))
    await db_session.flush()


async def test_no_tickets_no_alerts(service: AlertService) -> None:
    report = await service.check(NOW)

    assert (report.processed, report.human_share, report.alerts) == (0, None, [])
    assert report.queue.oldest_created_at is None


async def test_too_many_calls(
    service: AlertService, db_session: AsyncSession, customer: User
) -> None:
    queued = {"status": TicketStatus.IN_HUMAN_QUEUE, "route": Route.HUMAN}
    await add(db_session, customer, 3, due_at=NOW + timedelta(hours=1), **queued)
    await add(db_session, customer, 1)
    # Fuera de la ventana de 24 h: no cuenta.
    await add(db_session, customer, 5, created_at=NOW - timedelta(days=2))

    report = await service.check(NOW)

    assert (report.processed, report.human_share) == (4, 0.75)
    assert report.alerts == ["75% de tickets a persona en 24 h (máximo 65%)"]


async def test_queue_overdue_and_oldest(
    service: AlertService, db_session: AsyncSession, customer: User
) -> None:
    queued = {"status": TicketStatus.IN_HUMAN_QUEUE, "route": Route.HUMAN}
    oldest = NOW - timedelta(hours=5)
    await add(
        db_session,
        customer,
        due_at=NOW - timedelta(hours=1),
        created_at=oldest,
        **queued,
    )
    await add(db_session, customer, due_at=NOW + timedelta(hours=1), **queued)
    # Contactado: ya está atendido, no cuenta como esperando.
    await add(
        db_session,
        customer,
        status=TicketStatus.CONTACTED,
        route=Route.HUMAN,
        due_at=NOW - timedelta(hours=3),
    )

    report = await service.check(NOW)

    assert (report.queue.waiting, report.queue.unassigned, report.queue.overdue) == (
        2,
        2,
        1,
    )
    assert report.queue.oldest_created_at == oldest
    assert "1 tickets en la cola con el plazo vencido" in report.alerts


async def test_stuck_processing(
    service: AlertService, db_session: AsyncSession, customer: User
) -> None:
    unprocessed: dict[str, Any] = {"route": None, "intent": None}
    await add(
        db_session,
        customer,
        status=TicketStatus.PROCESSING,
        created_at=NOW - timedelta(minutes=10),
        **unprocessed,
    )
    # Recién llegado: aún le da tiempo.
    await add(
        db_session,
        customer,
        status=TicketStatus.RECEIVED,
        created_at=NOW,
        **unprocessed,
    )

    report = await service.check(NOW)

    assert report.stuck == 1
    assert report.alerts == ["1 tickets sin procesar desde hace más de 5 min"]


async def test_escalation_rate_by_intent(
    service: AlertService, db_session: AsyncSession, customer: User
) -> None:
    escalated = {
        "status": TicketStatus.IN_HUMAN_QUEUE,
        "escalated_at": NOW,
        "due_at": NOW + timedelta(hours=4),
    }
    # age_limit: 2 de 5 escalan (40 %) → alerta.
    await add(db_session, customer, 3)
    await add(db_session, customer, 2, **escalated)
    # card_arrival: 1 de 2 (50 %), pero con 2 tickets no se puede saber.
    await add(db_session, customer, 1, intent="card_arrival")
    await add(db_session, customer, 1, intent="card_arrival", **escalated)

    report = await service.check(NOW)

    rates = {e.intent: (e.escalated, e.answered) for e in report.escalations}
    assert rates == {"age_limit": (2, 5), "card_arrival": (1, 2)}
    assert report.alerts == ["age_limit: 40% de escalados (2 de 5, máximo 15%)"]
