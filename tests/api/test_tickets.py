import uuid
from datetime import timedelta

import pytest
from fakes import FakeClassifier
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.config import get_settings
from revision_rag.models import Ticket, TicketEvent, TicketMessage, User
from revision_rag.models.enums import (
    MessageAuthor,
    MessageStatus,
    TicketEventType,
    TicketStatus,
)
from revision_rag.models.ticket import TICKET_TEXT_MAX_LENGTH
from revision_rag.triage.answers import CALL_TEMPLATE, CannedAnswers

TICKETS_URL = "/api/v1/tickets"
TEXT = "My card hasn't arrived yet"


async def create_ticket(
    client: AsyncClient, headers: dict[str, str], text: str = TEXT
) -> dict:
    response = await client.post(TICKETS_URL, json={"text": text}, headers=headers)
    assert response.status_code == 202
    return response.json()


# --- POST /tickets ------------------------------------------------------------


async def test_create_returns_202_with_the_processed_ticket(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    # El clasificador falso duda por defecto → persona ("low confidence").
    response = await client.post(
        TICKETS_URL, json={"text": f"  {TEXT}  "}, headers=auth_headers
    )

    assert response.status_code == 202
    body = response.json()
    assert body["text"] == TEXT  # sin los espacios de los extremos
    # 202 aunque ya esté procesado: la API no cambiará cuando llegue ARQ (v2).
    assert body["status"] == "in_human_queue"
    assert body["ticket_type"] == "cards_ordering_and_activation"
    # Lo interno (intención, prioridad, motivos) no sale nunca al cliente.
    assert set(body) == {
        "id",
        "text",
        "status",
        "ticket_type",
        "created_at",
        "updated_at",
    }
    assert response.headers["location"].endswith(f"{TICKETS_URL}/{body['id']}")


async def test_create_writes_the_history(
    client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    body = await create_ticket(client, auth_headers)

    events = (
        await db_session.scalars(
            select(TicketEvent).order_by(TicketEvent.created_at, TicketEvent.id)
        )
    ).all()
    customer_id = await db_session.scalar(
        select(Ticket.customer_id).where(Ticket.id == uuid.UUID(body["id"]))
    )
    # created lo provoca el cliente; los cambios de estado, el sistema (None).
    assert [(e.event, e.from_status, e.to_status, e.actor_id) for e in events] == [
        (TicketEventType.CREATED, None, None, customer_id),
        (TicketEventType.STATUS_CHANGED, "received", "processing", None),
        (TicketEventType.STATUS_CHANGED, "processing", "in_human_queue", None),
    ]


async def test_faq_ticket_is_answered_with_its_canned_answer(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    canned_answers: CannedAnswers,
) -> None:
    text = "What is the age limit to open an account?"
    classifier.answer(text, "age_limit")

    body = await create_ticket(client, auth_headers, text)
    detail = (
        await client.get(f"{TICKETS_URL}/{body['id']}", headers=auth_headers)
    ).json()

    assert body["status"] == "answered"
    assert [(m["author"], m["content"]) for m in detail["messages"]] == [
        ("customer", text),
        ("system", canned_answers.for_intent("age_limit")),
    ]


async def test_stolen_card_goes_to_a_person_with_the_call_template(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    db_session: AsyncSession,
) -> None:
    text = "My card was stolen"
    classifier.answer(text, "lost_or_stolen_card")

    body = await create_ticket(client, auth_headers, text)
    detail = (
        await client.get(f"{TICKETS_URL}/{body['id']}", headers=auth_headers)
    ).json()
    ticket = await db_session.get(Ticket, uuid.UUID(body["id"]))

    assert body["status"] == "in_human_queue"
    assert detail["messages"][-1] == {
        "author": "system",
        "content": CALL_TEMPLATE,
        "created_at": detail["messages"][-1]["created_at"],
    }
    assert ticket is not None
    assert ticket.route_reason == "sensitive keyword"
    assert ticket.priority == 3
    assert ticket.due_at is not None
    assert (ticket.due_at - ticket.created_at).total_seconds() == 3600


async def test_processing_error_still_answers_and_goes_to_a_person(
    client: AsyncClient, auth_headers: dict[str, str], classifier: FakeClassifier
) -> None:
    classifier.error = RuntimeError("model exploded")

    response = await client.post(TICKETS_URL, json={"text": TEXT}, headers=auth_headers)

    # El cliente no ve el fallo: su ticket está creado y en la cola humana.
    assert response.status_code == 202
    assert response.json()["status"] == "in_human_queue"


@pytest.mark.parametrize(
    "text",
    ["", "   ", "x" * (TICKET_TEXT_MAX_LENGTH + 1)],
    ids=["vacío", "solo espacios", "demasiado largo"],
)
async def test_create_with_invalid_text_returns_422(
    client: AsyncClient, auth_headers: dict[str, str], text: str
) -> None:
    response = await client.post(TICKETS_URL, json={"text": text}, headers=auth_headers)

    assert response.status_code == 422


async def test_create_without_token_returns_401(client: AsyncClient) -> None:
    response = await client.post(TICKETS_URL, json={"text": TEXT})

    assert response.status_code == 401


async def test_agent_cannot_create_tickets(
    client: AsyncClient, agent_headers: dict[str, str]
) -> None:
    response = await client.post(
        TICKETS_URL, json={"text": TEXT}, headers=agent_headers
    )

    assert response.status_code == 403


async def test_customer_without_phone_gets_409(
    client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    # Como los usuarios creados antes de que el teléfono fuera obligatorio.
    await db_session.execute(update(User).values(phone=None))

    response = await client.post(TICKETS_URL, json={"text": TEXT}, headers=auth_headers)

    assert response.status_code == 409
    assert "teléfono" in response.json()["detail"]


# --- Rate limiting ------------------------------------------------------------


async def test_rate_limit_returns_429_with_retry_after(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    settings = get_settings()
    for _ in range(settings.ticket_rate_limit):
        await create_ticket(client, auth_headers)

    response = await client.post(TICKETS_URL, json={"text": TEXT}, headers=auth_headers)

    assert response.status_code == 429
    assert (
        0 < int(response.headers["retry-after"]) <= settings.ticket_rate_window_seconds
    )


async def test_rate_limit_is_per_customer(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    for _ in range(get_settings().ticket_rate_limit):
        await create_ticket(client, auth_headers)

    await create_ticket(client, other_auth_headers)


# --- Idempotency-Key ----------------------------------------------------------


async def test_same_idempotency_key_returns_the_same_ticket(
    client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    headers = auth_headers | {"Idempotency-Key": "retry-1"}

    first = await create_ticket(client, headers)
    second = await create_ticket(client, headers)

    assert second["id"] == first["id"]
    assert len((await db_session.scalars(select(Ticket))).all()) == 1


async def test_retries_do_not_count_for_the_rate_limit(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = auth_headers | {"Idempotency-Key": "retry-1"}
    for _ in range(get_settings().ticket_rate_limit + 2):
        await create_ticket(client, headers)

    # El cupo sigue entero menos uno: los reintentos no han gastado nada.
    await create_ticket(client, auth_headers)


async def test_same_key_with_other_text_returns_422(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = auth_headers | {"Idempotency-Key": "retry-1"}
    await create_ticket(client, headers)

    response = await client.post(
        TICKETS_URL, json={"text": "Something else"}, headers=headers
    )

    assert response.status_code == 422
    assert "Idempotency-Key" in response.json()["detail"]


async def test_idempotency_key_is_per_customer(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    key = {"Idempotency-Key": "retry-1"}
    mine = await create_ticket(client, auth_headers | key)
    theirs = await create_ticket(client, other_auth_headers | key)

    # La misma key de otro cliente no le devuelve mi ticket.
    assert theirs["id"] != mine["id"]


async def test_rate_limited_request_does_not_save_the_key(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    headers = auth_headers | {"Idempotency-Key": "retry-1"}
    for _ in range(get_settings().ticket_rate_limit):
        await create_ticket(client, auth_headers)

    rejected = await client.post(TICKETS_URL, json={"text": TEXT}, headers=headers)
    retried = await client.post(TICKETS_URL, json={"text": TEXT}, headers=headers)

    # El 429 no ha creado nada: el reintento vuelve a contar como ticket nuevo
    # (y vuelve a dar 429), no devuelve un ticket fantasma.
    assert rejected.status_code == 429
    assert retried.status_code == 429


# --- GET /tickets y GET /tickets/{id} -----------------------------------------


async def test_list_returns_only_my_tickets_newest_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    first = await create_ticket(client, auth_headers, "first")
    second = await create_ticket(client, auth_headers, "second")
    await create_ticket(client, other_auth_headers, "not mine")

    response = await client.get(TICKETS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert [t["id"] for t in response.json()] == [second["id"], first["id"]]


async def test_list_is_paginated(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for text in ("a", "b", "c"):
        await create_ticket(client, auth_headers, text)

    response = await client.get(
        TICKETS_URL, params={"limit": 2, "offset": 1}, headers=auth_headers
    )

    assert [t["text"] for t in response.json()] == ["b", "a"]


async def test_detail_shows_the_conversation_without_drafts(
    client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    body = await create_ticket(client, auth_headers)
    ticket_id = uuid.UUID(body["id"])
    db_session.add_all(
        [
            TicketMessage(
                ticket_id=ticket_id,
                author=MessageAuthor.SYSTEM,
                content="Cards usually arrive in 5-7 working days.",
            ),
            TicketMessage(
                ticket_id=ticket_id,
                author=MessageAuthor.AI,
                content="Rejected by the judge",
                status=MessageStatus.DRAFT,
            ),
            TicketMessage(
                ticket_id=ticket_id,
                author=MessageAuthor.CUSTOMER,
                content="It's been 3 weeks",
            ),
        ]
    )
    await db_session.flush()

    response = await client.get(f"{TICKETS_URL}/{ticket_id}", headers=auth_headers)

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "in_human_queue"
    assert [(m["author"], m["content"]) for m in detail["messages"]] == [
        ("customer", TEXT),
        ("system", CALL_TEMPLATE),  # la respuesta del procesado
        ("system", "Cards usually arrive in 5-7 working days."),
        ("customer", "It's been 3 weeks"),
    ]


async def test_detail_of_someone_elses_ticket_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    theirs = await create_ticket(client, other_auth_headers)

    response = await client.get(f"{TICKETS_URL}/{theirs['id']}", headers=auth_headers)

    # 404 y no 403: no se confirma que el ticket exista.
    assert response.status_code == 404
    assert response.json() == {"detail": "Ticket no encontrado"}


async def test_detail_of_a_missing_ticket_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(f"{TICKETS_URL}/{uuid.uuid7()}", headers=auth_headers)

    assert response.status_code == 404


@pytest.mark.parametrize("path", ["", f"/{uuid.uuid7()}"], ids=["lista", "detalle"])
async def test_agents_use_their_own_endpoints(
    client: AsyncClient, agent_headers: dict[str, str], path: str
) -> None:
    response = await client.get(f"{TICKETS_URL}{path}", headers=agent_headers)

    assert response.status_code == 403


# --- POST /tickets/{id}/escalate y /messages ----------------------------------

FAQ = "What is the age limit to open an account?"


async def answered_ticket(
    client: AsyncClient, headers: dict[str, str], classifier: FakeClassifier
) -> dict:
    """Un ticket respondido con una predefinida (age_limit: prioridad low)."""
    classifier.answer(FAQ, "age_limit")
    body = await create_ticket(client, headers, FAQ)
    assert body["status"] == "answered"
    return body


async def events_of(db_session: AsyncSession, ticket_id: str) -> list[str]:
    rows = await db_session.scalars(
        select(TicketEvent.event)
        .where(TicketEvent.ticket_id == uuid.UUID(ticket_id))
        .order_by(TicketEvent.created_at, TicketEvent.id)
    )
    return list(rows)


async def test_escalating_sends_the_ticket_to_a_person(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    db_session: AsyncSession,
    canned_answers: CannedAnswers,
) -> None:
    body = await answered_ticket(client, auth_headers, classifier)

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/escalate", headers=auth_headers
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["status"] == "in_human_queue"
    assert [(m["author"], m["content"]) for m in detail["messages"]] == [
        ("customer", FAQ),
        ("system", canned_answers.for_intent("age_limit")),
        ("system", CALL_TEMPLATE),
    ]
    ticket = await db_session.get(Ticket, uuid.UUID(body["id"]))
    assert ticket is not None and ticket.escalated_at is not None
    assert (ticket.priority, ticket.priority_reason) == (
        2,
        "low by intent, raised to medium by escalation",
    )
    # El plazo cuenta desde que escala (medium: 4 h), no desde que escribió.
    assert ticket.due_at == ticket.escalated_at + timedelta(hours=4)
    # route no cambia: es lo que decidió el sistema (métricas, correcciones).
    assert ticket.route == "ai"


async def test_escalating_twice_does_nothing_the_second_time(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    db_session: AsyncSession,
) -> None:
    body = await answered_ticket(client, auth_headers, classifier)
    url = f"{TICKETS_URL}/{body['id']}/escalate"

    first = await client.post(url, headers=auth_headers)
    second = await client.post(url, headers=auth_headers)

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["messages"] == first.json()["messages"]
    assert (await events_of(db_session, body["id"])).count("escalated") == 1


async def resolve_directly(db_session: AsyncSession, ticket_id: str) -> None:
    await db_session.execute(
        update(Ticket)
        .where(Ticket.id == uuid.UUID(ticket_id))
        .values(status=TicketStatus.RESOLVED)
    )


@pytest.mark.parametrize("action", ["escalate", "messages"])
async def test_a_resolved_ticket_cannot_be_reopened(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    action: str,
) -> None:
    body = await create_ticket(client, auth_headers)
    await resolve_directly(db_session, body["id"])

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/{action}",
        json={"text": "It still doesn't work"},
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert "abre uno nuevo" in response.json()["detail"]


async def test_replying_to_an_answer_escalates_with_the_text(
    client: AsyncClient,
    auth_headers: dict[str, str],
    classifier: FakeClassifier,
    db_session: AsyncSession,
) -> None:
    body = await answered_ticket(client, auth_headers, classifier)
    reply = "That doesn't answer my question"

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/messages",
        json={"text": f"  {reply} "},
        headers=auth_headers,
    )

    assert response.status_code == 201
    detail = response.json()
    assert detail["status"] == "in_human_queue"
    assert [(m["author"], m["content"]) for m in detail["messages"]][2:] == [
        ("customer", reply),
        ("system", CALL_TEMPLATE),
    ]
    assert "escalated" in await events_of(db_session, body["id"])


async def test_replying_while_with_a_person_is_attached_for_the_agent(
    client: AsyncClient, auth_headers: dict[str, str], db_session: AsyncSession
) -> None:
    body = await create_ticket(client, auth_headers)  # duda → persona
    ticket = await db_session.get(Ticket, uuid.UUID(body["id"]))
    assert ticket is not None
    before = (ticket.priority, ticket.due_at)

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/messages",
        json={"text": "Call me after 6 pm please"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    detail = response.json()
    assert detail["status"] == "in_human_queue"
    assert detail["messages"][-1]["content"] == "Call me after 6 pm please"
    assert (await events_of(db_session, body["id"]))[-1] == "customer_replied"
    # Ni prioridad ni plazo cambian: no es un ticket nuevo.
    await db_session.refresh(ticket)
    assert (ticket.priority, ticket.due_at) == before


@pytest.mark.parametrize(
    "text",
    ["", "   ", "x" * (TICKET_TEXT_MAX_LENGTH + 1)],
    ids=["vacío", "solo espacios", "demasiado largo"],
)
async def test_reply_with_invalid_text_returns_422(
    client: AsyncClient, auth_headers: dict[str, str], text: str
) -> None:
    body = await create_ticket(client, auth_headers)

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/messages",
        json={"text": text},
        headers=auth_headers,
    )

    assert response.status_code == 422


@pytest.mark.parametrize("action", ["escalate", "messages"])
async def test_cannot_write_on_someone_elses_ticket(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
    action: str,
) -> None:
    body = await create_ticket(client, auth_headers)

    response = await client.post(
        f"{TICKETS_URL}/{body['id']}/{action}",
        json={"text": "hello"},
        headers=other_auth_headers,
    )

    assert response.status_code == 404
