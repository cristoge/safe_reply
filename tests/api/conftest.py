import pytest
from httpx import AsyncClient


async def login_headers(client: AsyncClient, email: str) -> dict[str, str]:
    """Registra al usuario, hace login y devuelve la cabecera con su access token."""
    credentials = {"email": email, "password": "contraseña123"}
    response = await client.post("/api/v1/auth/register", json=credentials)
    assert response.status_code == 201
    response = await client.post("/api/v1/auth/login", json=credentials)
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Usuario logueado con el que trabaja el test (cristo@test.com)."""
    return await login_headers(client, "cristo@test.com")


@pytest.fixture
async def other_auth_headers(client: AsyncClient) -> dict[str, str]:
    """Un segundo usuario, para comprobar que no ve lo del primero."""
    return await login_headers(client, "otra@test.com")
