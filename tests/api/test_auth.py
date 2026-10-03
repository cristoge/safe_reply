import pytest
from httpx import AsyncClient

REGISTER_URL = "/api/v1/auth/register"


async def test_register_returns_201_with_the_user(client: AsyncClient) -> None:
    response = await client.post(
        REGISTER_URL,
        json={"email": "Cristo@Test.com", "password": "contraseña123"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "cristo@test.com"
    assert body["display_name"] is None
    assert set(body) == {"id", "email", "display_name", "created_at"}


async def test_register_response_never_includes_the_password(
    client: AsyncClient,
) -> None:
    response = await client.post(
        REGISTER_URL,
        json={"email": "cristo@test.com", "password": "contraseña123"},
    )

    assert "password" not in response.text
    assert "argon2" not in response.text


async def test_register_with_existing_email_returns_409(client: AsyncClient) -> None:
    payload = {"email": "cristo@test.com", "password": "contraseña123"}
    await client.post(REGISTER_URL, json=payload)

    response = await client.post(
        REGISTER_URL, json={**payload, "email": "CRISTO@test.com"}
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Ese email ya está registrado"}


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "no-es-un-email", "password": "contraseña123"},
        {"email": "cristo@test.com", "password": "corta"},
        {"email": "cristo@test.com"},
        {"password": "contraseña123"},
    ],
    ids=["email inválido", "contraseña corta", "sin contraseña", "sin email"],
)
async def test_register_with_invalid_data_returns_422(
    client: AsyncClient, payload: dict[str, str]
) -> None:
    response = await client.post(REGISTER_URL, json=payload)

    assert response.status_code == 422
