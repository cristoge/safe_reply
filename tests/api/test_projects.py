import uuid
from typing import Any

import pytest
from httpx import AsyncClient

PROJECTS_URL = "/api/v1/projects"


async def create_project(
    client: AsyncClient, headers: dict[str, str], name: str = "AWS"
) -> dict[str, Any]:
    response = await client.post(PROJECTS_URL, json={"name": name}, headers=headers)
    assert response.status_code == 201
    return response.json()


async def test_create_project_returns_201(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.post(
        PROJECTS_URL,
        json={"name": "  AWS  ", "description": "Notas del curso"},
        headers=auth_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "AWS"
    assert body["description"] == "Notas del curso"
    assert set(body) == {"id", "name", "description", "created_at", "updated_at"}


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": ""}, {"name": "   "}, {"name": "x" * 201}],
    ids=["sin nombre", "nombre vacío", "solo espacios", "nombre largo"],
)
async def test_create_project_with_invalid_name_returns_422(
    client: AsyncClient, auth_headers: dict[str, str], payload: dict[str, str]
) -> None:
    response = await client.post(PROJECTS_URL, json=payload, headers=auth_headers)

    assert response.status_code == 422


async def test_list_projects_returns_only_mine_newest_first(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
) -> None:
    await create_project(client, auth_headers, "Primero")
    await create_project(client, other_auth_headers, "De otro")
    await create_project(client, auth_headers, "Segundo")

    response = await client.get(PROJECTS_URL, headers=auth_headers)

    assert response.status_code == 200
    assert [project["name"] for project in response.json()] == ["Segundo", "Primero"]


async def test_list_projects_returns_5_by_default(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    for i in range(6):
        await create_project(client, auth_headers, f"Proyecto {i}")

    response = await client.get(PROJECTS_URL, headers=auth_headers)

    assert len(response.json()) == 5


async def test_read_project(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    project = await create_project(client, auth_headers)

    response = await client.get(f"{PROJECTS_URL}/{project['id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == project


async def test_update_project_changes_only_the_fields_sent(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    project = await create_project(client, auth_headers)
    url = f"{PROJECTS_URL}/{project['id']}"

    response = await client.patch(
        url, json={"description": "Nueva"}, headers=auth_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "AWS"
    assert body["description"] == "Nueva"
    # updated_at lo pone Postgres en el UPDATE y tiene que llegar en la respuesta
    # (sin eager_defaults, leerlo daba MissingGreenlet). Es >= y no >: now() es
    # la hora de inicio de la transacción, y en los tests todo va en una sola.
    assert body["updated_at"] >= project["updated_at"]


async def test_update_project_name_cannot_be_null(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    project = await create_project(client, auth_headers)

    response = await client.patch(
        f"{PROJECTS_URL}/{project['id']}", json={"name": None}, headers=auth_headers
    )

    assert response.status_code == 422


async def test_delete_project(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    project = await create_project(client, auth_headers)
    url = f"{PROJECTS_URL}/{project['id']}"

    response = await client.delete(url, headers=auth_headers)

    assert response.status_code == 204
    assert (await client.get(url, headers=auth_headers)).status_code == 404


@pytest.mark.parametrize("method", ["GET", "PATCH", "DELETE"])
async def test_project_of_another_user_returns_404(
    client: AsyncClient,
    auth_headers: dict[str, str],
    other_auth_headers: dict[str, str],
    method: str,
) -> None:
    # IDOR: conocer el id de un proyecto ajeno no sirve para verlo ni tocarlo.
    project = await create_project(client, other_auth_headers, "De otro")
    url = f"{PROJECTS_URL}/{project['id']}"

    response = await client.request(
        method, url, json={"name": "Hackeado"}, headers=auth_headers
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Proyecto no encontrado"}
    # El proyecto sigue intacto para su dueño.
    response = await client.get(url, headers=other_auth_headers)
    assert response.json()["name"] == "De otro"


async def test_project_that_does_not_exist_returns_404(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(f"{PROJECTS_URL}/{uuid.uuid7()}", headers=auth_headers)

    assert response.status_code == 404


async def test_project_id_that_is_not_a_uuid_returns_422(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.get(f"{PROJECTS_URL}/no-es-un-uuid", headers=auth_headers)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", ""),
        ("GET", ""),
        ("GET", f"/{uuid.uuid7()}"),
        ("PATCH", f"/{uuid.uuid7()}"),
        ("DELETE", f"/{uuid.uuid7()}"),
    ],
)
async def test_projects_without_token_return_401(
    client: AsyncClient, method: str, path: str
) -> None:
    response = await client.request(method, f"{PROJECTS_URL}{path}", json={})

    assert response.status_code == 401
