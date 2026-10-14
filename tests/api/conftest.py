from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from revision_rag.api.deps import get_storage
from revision_rag.clients.storage import LocalStorage
from revision_rag.main import app


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


@pytest.fixture(autouse=True)
def storage(tmp_path: Path) -> Iterator[LocalStorage]:
    """Storage en una carpeta temporal (una por test): nunca se escribe en data/."""
    test_storage = LocalStorage(tmp_path)
    app.dependency_overrides[get_storage] = lambda: test_storage
    yield test_storage
    app.dependency_overrides.pop(get_storage, None)
