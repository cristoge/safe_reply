from collections.abc import AsyncIterator

from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from safe_reply.db.cache import build_redis, get_redis
from safe_reply.db.session import get_db
from safe_reply.main import app


async def test_health_returns_ok_when_everything_is_up(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


async def test_health_returns_503_when_database_is_down(client: AsyncClient) -> None:
    # Un puerto donde no escucha nadie: simula la base de datos caída.
    broken_engine = create_async_engine("postgresql+psycopg://rag:rag@localhost:1/rag")

    async def broken_db() -> AsyncIterator[AsyncSession]:
        async with AsyncSession(broken_engine) as session:
            yield session

    app.dependency_overrides[get_db] = broken_db

    response = await client.get("/health")
    await broken_engine.dispose()

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "database": "unreachable",
        "redis": "ok",
    }


async def test_health_returns_503_when_redis_is_down(client: AsyncClient) -> None:
    broken_redis = build_redis("redis://localhost:1/0")

    def broken_get_redis() -> Redis:
        return broken_redis

    app.dependency_overrides[get_redis] = broken_get_redis

    response = await client.get("/health")
    await broken_redis.aclose()

    assert response.status_code == 503
    assert response.json() == {
        "status": "error",
        "database": "ok",
        "redis": "unreachable",
    }
