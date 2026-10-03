import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import EmailAlreadyRegisteredError
from revision_rag.core.security import verify_password
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.user import UserCreate
from revision_rag.services.auth import AuthService


@pytest.fixture
def users(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)


@pytest.fixture
def service(db_session: AsyncSession, users: UserRepository) -> AuthService:
    return AuthService(db_session, users)


async def test_register_creates_user_with_hashed_password(
    service: AuthService, users: UserRepository
) -> None:
    user = await service.register(
        UserCreate(
            email="Cristo@Test.com", password="contraseña123", display_name="Cristo"
        )
    )

    saved = await users.get_by_email("cristo@test.com")
    assert saved is not None
    assert saved.id == user.id
    assert saved.email == "cristo@test.com"
    assert saved.hashed_password != "contraseña123"
    assert await verify_password("contraseña123", saved.hashed_password)


async def test_register_with_existing_email_fails_and_keeps_the_first_user(
    service: AuthService, users: UserRepository
) -> None:
    first = await service.register(
        UserCreate(email="cristo@test.com", password="contraseña123")
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(
            UserCreate(email="CRISTO@test.com", password="otra12345")
        )

    # El rollback solo deshace el registro fallido: el primero ya estaba confirmado.
    saved = await users.get_by_email("cristo@test.com")
    assert saved is not None
    assert saved.id == first.id
