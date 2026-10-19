import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from fakes import FakeClassifier
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from revision_rag.api.deps import get_canned_answers, get_classifier, get_rules
from revision_rag.core.config import Settings, get_settings
from revision_rag.db.cache import build_redis, get_redis
from revision_rag.db.session import get_db
from revision_rag.main import app
from revision_rag.triage.answers import CannedAnswers
from revision_rag.triage.rules import Rules, load_rules

_settings = Settings()  # pyright: ignore[reportCallIssue]

# Base de datos solo para tests: el mismo Postgres de Docker, otra base de datos.
# Se apunta DATABASE_URL a ella antes de que nada lea la configuración, así la
# app y Alembic la usan sin cambiar nada de su código.
TEST_DATABASE_URL = make_url(_settings.database_url).set(database="rag_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL.render_as_string(hide_password=False)

# Lo mismo con Redis, cuyas bases de datos no tienen nombre sino número (0-15):
# la de desarrollo es la 0 y la de tests la 1.
TEST_REDIS_URL = urlsplit(_settings.redis_url)._replace(path="/1").geturl()
os.environ["REDIS_URL"] = TEST_REDIS_URL
get_settings.cache_clear()

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
RULES_PATH = Path(__file__).parents[1] / "rules" / "intents.yaml"
CANNED_DIR = Path(__file__).parents[1] / "policies" / "canned"


async def _recreate_test_database() -> None:
    # CREATE/DROP DATABASE no pueden ir dentro de una transacción → AUTOCOMMIT,
    # y conectados a otra base de datos ("postgres"), no a la que se borra.
    admin = create_async_engine(
        TEST_DATABASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    name = TEST_DATABASE_URL.database
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    return Config(ALEMBIC_INI)


@pytest.fixture(scope="session", autouse=True)
def database(alembic_config: Config) -> None:
    """Base de datos de tests desde cero en cada ejecución, con todas las migraciones.

    Así los tests también comprueban que las migraciones se aplican sobre una
    base de datos vacía (el bug del punto 1 de la v1 no habría pasado).
    """
    asyncio.run(_recreate_test_database())
    command.upgrade(alembic_config, "head")


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(TEST_DATABASE_URL)
    yield engine
    await engine.dispose()


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Cada test corre dentro de una transacción que se deshace al terminar.

    Los tests no se ven entre sí y la base de datos queda siempre vacía.
    Con join_transaction_mode="create_savepoint", un commit() dentro del código
    probado solo cierra un savepoint: la transacción de fuera sigue abierta.
    """
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(
            bind=conn,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        yield session
        await session.close()
        await transaction.rollback()


@pytest.fixture(scope="session")
async def redis_client() -> AsyncIterator[Redis]:
    client = build_redis(TEST_REDIS_URL)
    yield client
    await client.aclose()


@pytest.fixture
async def redis(redis_client: Redis) -> Redis:
    """Cada test empieza con Redis vacío. FLUSHDB solo borra la base de datos 1."""
    await redis_client.flushdb()
    return redis_client


@pytest.fixture(scope="session")
def rules() -> Rules:
    """Las reglas reales (rules/intents.yaml)."""
    return load_rules(RULES_PATH)


@pytest.fixture
def classifier(rules: Rules) -> FakeClassifier:
    return FakeClassifier(frozenset(rules.intents))


@pytest.fixture(scope="session")
def canned_answers() -> CannedAnswers:
    """Las respuestas reales (policies/canned/)."""
    return CannedAnswers.from_dir(CANNED_DIR)


@pytest.fixture
async def client(
    db_session: AsyncSession,
    redis: Redis,
    rules: Rules,
    classifier: FakeClassifier,
    canned_answers: CannedAnswers,
) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, sin levantar servidor.

    Con la sesión y el Redis del test, las reglas reales y el clasificador
    falso. ASGITransport no ejecuta el lifespan: lo que este carga en app.state
    se inyecta aquí.
    """

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    def override_get_redis() -> Redis:
        return redis

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_redis] = override_get_redis
    app.dependency_overrides[get_rules] = lambda: rules
    app.dependency_overrides[get_classifier] = lambda: classifier
    app.dependency_overrides[get_canned_answers] = lambda: canned_answers
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
