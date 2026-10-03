import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from revision_rag.core.config import Settings, get_settings
from revision_rag.db.session import get_db
from revision_rag.main import app

# Base de datos solo para tests: el mismo Postgres de Docker, otra base de datos.
# Se apunta DATABASE_URL a ella antes de que nada lea la configuración, así la
# app y Alembic la usan sin cambiar nada de su código.
TEST_DATABASE_URL = make_url(Settings().database_url).set(database="rag_test")
os.environ["DATABASE_URL"] = TEST_DATABASE_URL.render_as_string(hide_password=False)
get_settings.cache_clear()

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"


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


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
    """Cliente HTTP contra la app, sin levantar servidor, usando la sesión del test."""

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
