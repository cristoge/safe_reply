from collections.abc import AsyncIterator

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from revision_rag.db.session import get_db
from revision_rag.main import app


async def test_health_returns_ok_when_database_is_up(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


async def test_health_returns_503_when_database_is_down(client: AsyncClient) -> None:
    # Un puerto donde no escucha nadie: simula la base de datos caída.
    broken_engine = create_async_engine("postgresql+asyncpg://rag:rag@localhost:1/rag")

    async def broken_db() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(broken_engine) as session:
            yield session

    app.dependency_overrides[get_db] = broken_db

    response = await client.get("/health")
    await broken_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}
