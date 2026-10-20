import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from safe_reply.core.config import get_settings
from safe_reply.main import include_routers

USERS_URL = "/api/v1/users"
ME_URL = "/api/v1/users/me"


async def register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "contraseña123", "phone": "+34600123456"},
    )
    assert response.status_code == 201


async def test_me_returns_the_logged_in_user(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    await register(client, "otra@test.com")

    response = await client.get(ME_URL, headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["email"] == "cristo@test.com"
    assert "hashed_password" not in response.text


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer no-es-un-jwt"}, {"Authorization": "Basic abc"}],
    ids=["sin cabecera", "token inválido", "otro esquema"],
)
async def test_me_without_a_valid_token_returns_401(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(ME_URL, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"detail": "No autenticado"}
    assert response.headers["www-authenticate"] == "Bearer"


async def test_update_me_changes_only_the_fields_sent(
    client: AsyncClient, auth_headers: dict[str, str]
) -> None:
    response = await client.patch(
        ME_URL, json={"display_name": "Cristo"}, headers=auth_headers
    )
    assert response.status_code == 200
    assert response.json()["display_name"] == "Cristo"

    # Body vacío: no cambia nada.
    response = await client.patch(ME_URL, json={}, headers=auth_headers)
    assert response.json()["display_name"] == "Cristo"

    # null explícito: lo borra.
    response = await client.patch(
        ME_URL, json={"display_name": None}, headers=auth_headers
    )
    assert response.json()["display_name"] is None


def build_app(environment: str) -> FastAPI:
    app = FastAPI()
    include_routers(app, get_settings().model_copy(update={"environment": environment}))
    return app


async def test_list_users_only_exists_in_development() -> None:
    development = build_app("development")
    production = build_app("production")

    # En /docs solo aparece en desarrollo...
    assert USERS_URL in development.openapi()["paths"]
    assert USERS_URL not in production.openapi()["paths"]
    assert ME_URL in production.openapi()["paths"]
    # ...y en producción la ruta no existe.
    transport = ASGITransport(app=production)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(USERS_URL)
    assert response.status_code == 404


async def test_list_users_empty(client: AsyncClient) -> None:
    response = await client.get(USERS_URL)

    assert response.status_code == 200
    assert response.json() == []


async def test_list_users_returns_them_in_creation_order_without_hash(
    client: AsyncClient,
) -> None:
    for email in ("a@test.com", "b@test.com", "c@test.com"):
        await register(client, email)

    response = await client.get(USERS_URL)

    assert response.status_code == 200
    assert [user["email"] for user in response.json()] == [
        "a@test.com",
        "b@test.com",
        "c@test.com",
    ]
    assert "hashed_password" not in response.text


async def test_list_users_paginates(client: AsyncClient) -> None:
    for email in ("a@test.com", "b@test.com", "c@test.com"):
        await register(client, email)

    response = await client.get(USERS_URL, params={"limit": 2, "offset": 1})

    assert [user["email"] for user in response.json()] == ["b@test.com", "c@test.com"]


async def test_list_users_returns_5_by_default(client: AsyncClient) -> None:
    for i in range(6):
        await register(client, f"user{i}@test.com")

    response = await client.get(USERS_URL)

    assert len(response.json()) == 5


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}],
    ids=["limit 0", "limit > 100", "offset negativo"],
)
async def test_list_users_with_invalid_pagination_returns_422(
    client: AsyncClient, params: dict[str, int]
) -> None:
    response = await client.get(USERS_URL, params=params)

    assert response.status_code == 422
