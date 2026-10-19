import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.security import hash_password
from revision_rag.models import User
from revision_rag.models.enums import UserRole

PASSWORD = "contraseña123"
PHONE = "+34600123456"


async def login(client: AsyncClient, email: str) -> dict[str, str]:
    """Hace login y devuelve la cabecera con el access token."""
    response = await client.post(
        "/api/v1/auth/login", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def login_headers(client: AsyncClient, email: str) -> dict[str, str]:
    """Registra al cliente, hace login y devuelve la cabecera con su access token."""
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": PASSWORD, "phone": PHONE},
    )
    assert response.status_code == 201
    return await login(client, email)


@pytest.fixture
async def auth_headers(client: AsyncClient) -> dict[str, str]:
    """Cliente logueado con el que trabaja el test (cristo@test.com)."""
    return await login_headers(client, "cristo@test.com")


@pytest.fixture
async def other_auth_headers(client: AsyncClient) -> dict[str, str]:
    """Un segundo usuario, para comprobar que no ve lo del primero."""
    return await login_headers(client, "otra@test.com")


async def agent_login_headers(
    client: AsyncClient, db_session: AsyncSession, email: str
) -> dict[str, str]:
    """Crea un agente directamente en la BD (por la API no se puede) y hace login."""
    db_session.add(
        User(
            email=email,
            hashed_password=await hash_password(PASSWORD),
            role=UserRole.AGENT,
        )
    )
    await db_session.flush()
    return await login(client, email)


@pytest.fixture
async def agent_headers(
    client: AsyncClient, db_session: AsyncSession
) -> dict[str, str]:
    """Un agente logueado."""
    return await agent_login_headers(client, db_session, "agente@test.com")


@pytest.fixture
async def other_agent_headers(
    client: AsyncClient, db_session: AsyncSession
) -> dict[str, str]:
    """Un segundo agente, para comprobar que no actúa sobre los tickets del primero."""
    return await agent_login_headers(client, db_session, "luis@test.com")
