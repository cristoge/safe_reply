import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import User
from revision_rag.repositories.user import UserRepository


@pytest.fixture
def repo(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)


async def test_add_assigns_id_and_server_defaults(repo: UserRepository) -> None:
    user = await repo.add(User(email="cristo@test.com", hashed_password="x"))

    assert user.id.version == 7
    # created_at lo pone Postgres (server_default). Si no se hubiera traído en
    # el INSERT, leerlo aquí intentaría recargarlo y en async daría error.
    assert user.created_at is not None


async def test_get_by_email_ignores_case(repo: UserRepository) -> None:
    created = await repo.add(User(email="cristo@test.com", hashed_password="x"))

    found = await repo.get_by_email("Cristo@TEST.com")

    assert found is not None
    assert found.id == created.id


async def test_get_by_email_returns_none_when_missing(repo: UserRepository) -> None:
    assert await repo.get_by_email("nadie@test.com") is None


async def test_get_by_id(repo: UserRepository) -> None:
    created = await repo.add(User(email="cristo@test.com", hashed_password="x"))

    assert await repo.get_by_id(created.id) is created
    assert await repo.get_by_id(uuid.uuid7()) is None


async def test_add_with_duplicate_email_fails_on_flush(repo: UserRepository) -> None:
    await repo.add(User(email="cristo@test.com", hashed_password="x"))

    with pytest.raises(IntegrityError):
        await repo.add(User(email="CRISTO@test.com", hashed_password="x"))
