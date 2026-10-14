import pytest
from httpx import AsyncClient

from revision_rag.core.config import get_settings

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


LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/refresh"
LOGOUT_URL = "/api/v1/auth/logout"
CREDENTIALS = {"email": "cristo@test.com", "password": "contraseña123"}


async def login(client: AsyncClient) -> dict[str, str]:
    """Registra al usuario de CREDENTIALS, hace login y devuelve los tokens."""
    await client.post(REGISTER_URL, json=CREDENTIALS)
    response = await client.post(LOGIN_URL, json=CREDENTIALS)
    assert response.status_code == 200
    return response.json()


async def test_login_returns_the_tokens(client: AsyncClient) -> None:
    await client.post(REGISTER_URL, json=CREDENTIALS)

    # El email se normaliza también en el login.
    response = await client.post(
        LOGIN_URL, json={**CREDENTIALS, "email": "Cristo@Test.com"}
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"access_token", "refresh_token", "token_type", "expires_in"}
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == get_settings().access_token_expire_minutes * 60


async def test_login_gives_the_same_error_for_wrong_password_and_unknown_email(
    client: AsyncClient,
) -> None:
    await client.post(REGISTER_URL, json=CREDENTIALS)

    wrong_password = await client.post(
        LOGIN_URL, json={**CREDENTIALS, "password": "incorrecta"}
    )
    unknown_email = await client.post(
        LOGIN_URL, json={**CREDENTIALS, "email": "nadie@test.com"}
    )

    # Respuestas idénticas: no se puede averiguar qué emails están registrados.
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert wrong_password.json() == {"detail": "Email o contraseña incorrectos"}


async def test_refresh_returns_new_tokens_and_the_old_one_stops_working(
    client: AsyncClient,
) -> None:
    tokens = await login(client)

    response = await client.post(
        REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 200
    assert response.json()["refresh_token"] != tokens["refresh_token"]
    reused = await client.post(
        REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
    )
    assert reused.status_code == 401
    assert reused.json() == {"detail": "Refresh token no válido"}


async def test_logout_returns_204_and_the_refresh_token_stops_working(
    client: AsyncClient,
) -> None:
    tokens = await login(client)

    response = await client.post(
        LOGOUT_URL, json={"refresh_token": tokens["refresh_token"]}
    )

    assert response.status_code == 204
    after_logout = await client.post(
        REFRESH_URL, json={"refresh_token": tokens["refresh_token"]}
    )
    assert after_logout.status_code == 401
