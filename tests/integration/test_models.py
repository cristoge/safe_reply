import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import User


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


# Se ejecuta dos veces con el mismo email: si el rollback entre tests no
# funcionara, la segunda vez fallaría por el email repetido.
@pytest.mark.parametrize("run", [1, 2])
async def test_each_test_starts_with_an_empty_database(
    db_session: AsyncSession, run: int
) -> None:
    assert await count(db_session, User) == 0

    db_session.add(User(email="isolation@test.com", hashed_password="x"))
    await db_session.flush()

    assert await count(db_session, User) == 1


async def test_email_is_unique_ignoring_case(db_session: AsyncSession) -> None:
    db_session.add(User(email="cristo@test.com", hashed_password="x"))
    await db_session.flush()

    db_session.add(User(email="Cristo@Test.com", hashed_password="x"))
    with pytest.raises(IntegrityError, match="uq_users_email_lower"):
        await db_session.flush()
