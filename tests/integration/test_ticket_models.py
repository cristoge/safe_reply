"""El esquema de tickets: cada CHECK, los CASCADE / SET NULL y el orden de los mensajes.

Los CHECK se prueban contra Postgres porque son la última defensa: cualquier
camino que se salte Pydantic (un script, un bug) acaba aquí.
"""

from typing import Any

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import (
    Ticket,
    TicketCorrection,
    TicketEvent,
    TicketMessage,
    User,
)
from revision_rag.models.enums import (
    MessageAuthor,
    MessageStatus,
    Route,
    TicketEventType,
    TicketStatus,
    UserRole,
)
from revision_rag.models.ticket import TICKET_TEXT_MAX_LENGTH


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


@pytest.fixture
async def customer(db_session: AsyncSession) -> User:
    user = User(email="customer@test.com", hashed_password="x", phone="+34600123456")
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def agent(db_session: AsyncSession) -> User:
    user = User(email="agent@test.com", hashed_password="x", role=UserRole.AGENT)
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def ticket(db_session: AsyncSession, customer: User) -> Ticket:
    ticket = Ticket(customer=customer, text="My card hasn't arrived yet")
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def test_new_user_is_a_customer(db_session: AsyncSession) -> None:
    # Por la API nunca se crea un agente: el valor por defecto es el seguro.
    await db_session.execute(
        text(
            "INSERT INTO users (id, email, hashed_password) VALUES (gen_random_uuid(), 'a@test.com', 'x')"
        )
    )
    assert await db_session.scalar(text("SELECT role FROM users")) == "customer"


async def test_new_ticket_defaults(db_session: AsyncSession, ticket: Ticket) -> None:
    assert ticket.status == TicketStatus.RECEIVED
    assert ticket.intent is None
    assert ticket.route is None
    assert ticket.created_at is not None


# --- CHECK de tickets ---------------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"text": ""}, "ck_tickets_text_not_empty"),
        ({"text": "   "}, "ck_tickets_text_not_empty"),
        ({"text": "x" * (TICKET_TEXT_MAX_LENGTH + 1)}, "ck_tickets_text_max_length"),
        ({"confidence": -0.01}, "ck_tickets_confidence_range"),
        ({"confidence": 1.01}, "ck_tickets_confidence_range"),
        ({"priority": 0}, "ck_tickets_priority_range"),
        ({"priority": 4}, "ck_tickets_priority_range"),
    ],
)
async def test_ticket_checks_reject_invalid_values(
    db_session: AsyncSession, customer: User, fields: dict[str, Any], constraint: str
) -> None:
    db_session.add(Ticket(customer=customer, **({"text": "Help"} | fields)))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


@pytest.mark.parametrize(
    "fields",
    [
        {"text": "x" * TICKET_TEXT_MAX_LENGTH},
        {"confidence": 0.0, "priority": 1},
        {"confidence": 1.0, "priority": 3},
    ],
)
async def test_ticket_checks_accept_the_limits(
    db_session: AsyncSession, customer: User, fields: dict[str, Any]
) -> None:
    db_session.add(Ticket(customer=customer, **({"text": "Help"} | fields)))
    await db_session.flush()


@pytest.mark.parametrize(
    ("column", "constraint"),
    [
        ("status", "ck_tickets_ticket_status"),
        ("route", "ck_tickets_route"),
    ],
)
async def test_enum_columns_have_a_check_in_postgres(
    db_session: AsyncSession, ticket: Ticket, column: str, constraint: str
) -> None:
    # SQL directo: desde el ORM, SQLAlchemy rechazaría el valor antes de llegar
    # a Postgres y el test no probaría el CHECK.
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.execute(
            text(f"UPDATE tickets SET {column} = 'x' WHERE id = :id"),
            {"id": ticket.id},
        )


# --- CHECK de ticket_messages -------------------------------------------------


@pytest.mark.parametrize(
    ("fields", "constraint"),
    [
        ({"content": " "}, "ck_ticket_messages_content_not_empty"),
        (
            {"content": "x" * (TICKET_TEXT_MAX_LENGTH + 1)},
            "ck_ticket_messages_customer_content_max_length",
        ),
        (
            {"author": MessageAuthor.AI, "agent": True},
            "ck_ticket_messages_agent_only_for_agent",
        ),
        (
            {"author": MessageAuthor.SYSTEM, "status": MessageStatus.DRAFT},
            "ck_ticket_messages_draft_only_for_ai",
        ),
        (
            {"status": MessageStatus.DRAFT},
            "ck_ticket_messages_draft_only_for_ai",
        ),
    ],
)
async def test_message_checks_reject_invalid_values(
    db_session: AsyncSession,
    ticket: Ticket,
    agent: User,
    fields: dict[str, Any],
    constraint: str,
) -> None:
    values = {"author": MessageAuthor.CUSTOMER, "content": "Any news?"} | fields
    if values.pop("agent", False):
        values["agent_id"] = agent.id
    db_session.add(TicketMessage(ticket=ticket, **values))
    with pytest.raises(IntegrityError, match=constraint):
        await db_session.flush()


async def test_valid_messages_from_every_author(
    db_session: AsyncSession, ticket: Ticket, agent: User
) -> None:
    long_text = "x" * (TICKET_TEXT_MAX_LENGTH + 1)
    db_session.add_all(
        [
            TicketMessage(ticket=ticket, author=MessageAuthor.CUSTOMER, content="Hi"),
            # El límite de longitud es solo para lo que escribe el cliente.
            TicketMessage(
                ticket=ticket, author=MessageAuthor.SYSTEM, content=long_text
            ),
            TicketMessage(
                ticket=ticket,
                author=MessageAuthor.AI,
                content="Draft",
                status=MessageStatus.DRAFT,
                judge_passed=False,
                sources=[{"section": "Cards › Delivery", "version": 1}],
            ),
            TicketMessage(
                ticket=ticket,
                author=MessageAuthor.AGENT,
                agent_id=agent.id,
                content="As discussed on the phone…",
            ),
        ]
    )
    await db_session.flush()

    assert await count(db_session, TicketMessage) == 4


async def test_messages_of_one_transaction_keep_their_order(
    db_session: AsyncSession, ticket: Ticket
) -> None:
    # La respuesta del cliente y la plantilla se guardan juntas: mismo
    # created_at (now() = inicio de la transacción). El uuid7 desempata.
    contents = ["first", "second", "third"]
    for content in contents:
        db_session.add(
            TicketMessage(ticket=ticket, author=MessageAuthor.SYSTEM, content=content)
        )
        await db_session.flush()

    rows = (
        await db_session.execute(
            select(TicketMessage.created_at, TicketMessage.content).order_by(
                TicketMessage.created_at, TicketMessage.id
            )
        )
    ).all()

    assert len({row.created_at for row in rows}) == 1
    assert [row.content for row in rows] == contents


# --- CHECK de ticket_events y ticket_corrections ------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"event": TicketEventType.STATUS_CHANGED},
        {"event": TicketEventType.STATUS_CHANGED, "to_status": TicketStatus.ANSWERED},
        {"event": TicketEventType.CREATED, "to_status": TicketStatus.RECEIVED},
        {"event": TicketEventType.CALL_FAILED, "from_status": TicketStatus.CONTACTED},
    ],
)
async def test_event_statuses_only_on_status_changed(
    db_session: AsyncSession, ticket: Ticket, fields: dict[str, Any]
) -> None:
    db_session.add(TicketEvent(ticket=ticket, **fields))
    with pytest.raises(
        IntegrityError, match="ck_ticket_events_statuses_only_on_status_changed"
    ):
        await db_session.flush()


async def test_valid_events(db_session: AsyncSession, ticket: Ticket) -> None:
    db_session.add_all(
        [
            TicketEvent(ticket=ticket, event=TicketEventType.CREATED),
            TicketEvent(
                ticket=ticket,
                event=TicketEventType.STATUS_CHANGED,
                from_status=TicketStatus.RECEIVED,
                to_status=TicketStatus.PROCESSING,
            ),
            TicketEvent(
                ticket=ticket, event=TicketEventType.CALL_FAILED, reason="no answer"
            ),
        ]
    )
    await db_session.flush()

    assert await count(db_session, TicketEvent) == 3


@pytest.mark.parametrize("column", ["predicted_priority", "corrected_priority"])
async def test_correction_priorities_between_1_and_3(
    db_session: AsyncSession, ticket: Ticket, column: str
) -> None:
    db_session.add(TicketCorrection(ticket=ticket, **{column: 4}))
    with pytest.raises(IntegrityError, match=f"ck_ticket_corrections_{column}_range"):
        await db_session.flush()


# --- Borrados -----------------------------------------------------------------


async def make_full_ticket(
    db_session: AsyncSession, customer: User, agent: User
) -> Ticket:
    """Un ticket con algo de cada tabla, atendido por el agente."""
    ticket = Ticket(
        customer=customer,
        text="I sent money to my brother and he hasn't got it",
        status=TicketStatus.CONTACTED,
        route=Route.HUMAN,
        priority=3,
        assigned_agent_id=agent.id,
    )
    db_session.add_all(
        [
            ticket,
            TicketMessage(
                ticket=ticket,
                author=MessageAuthor.AGENT,
                agent_id=agent.id,
                content="Hi",
            ),
            TicketEvent(
                ticket=ticket, event=TicketEventType.ASSIGNED, actor_id=agent.id
            ),
            TicketCorrection(ticket=ticket, agent_id=agent.id, corrected_priority=2),
        ]
    )
    await db_session.flush()
    return ticket


async def test_deleting_an_agent_keeps_the_history(
    db_session: AsyncSession, customer: User, agent: User
) -> None:
    ticket = await make_full_ticket(db_session, customer, agent)

    await db_session.execute(delete(User).where(User.id == agent.id))

    # Selects de columnas: leen de la BD, no de los objetos ya cargados en la sesión.
    assert (
        await db_session.scalar(
            select(Ticket.assigned_agent_id).where(Ticket.id == ticket.id)
        )
        is None
    )
    assert await db_session.scalar(select(TicketMessage.agent_id)) is None
    assert await db_session.scalar(select(TicketEvent.actor_id)) is None
    assert await db_session.scalar(select(TicketCorrection.agent_id)) is None
    for model in (Ticket, TicketMessage, TicketEvent, TicketCorrection):
        assert await count(db_session, model) == 1


async def test_deleting_a_customer_deletes_their_tickets(
    db_session: AsyncSession, customer: User, agent: User
) -> None:
    await make_full_ticket(db_session, customer, agent)

    await db_session.execute(delete(User).where(User.id == customer.id))

    for model in (Ticket, TicketMessage, TicketEvent, TicketCorrection):
        assert await count(db_session, model) == 0
    assert await count(db_session, User) == 1  # el agente sigue
