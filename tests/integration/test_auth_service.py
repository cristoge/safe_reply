from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.core.config import get_settings
from safe_reply.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    NotAuthenticatedError,
)
from safe_reply.core.security import hash_refresh_token, verify_password
from safe_reply.models import User
from safe_reply.models.enums import UserRole
from safe_reply.repositories.refresh_token import RefreshTokenRepository
from safe_reply.repositories.user import UserRepository
from safe_reply.schemas.auth import LoginRequest
from safe_reply.schemas.user import AgentCreate, UserCreate
from safe_reply.services.auth import AuthService

PHONE = "+34600123456"
LOGIN = LoginRequest(email="cristo@test.com", password="contraseña123")


@pytest.fixture
def users(db_session: AsyncSession) -> UserRepository:
    return UserRepository(db_session)


@pytest.fixture
def refresh_tokens(db_session: AsyncSession) -> RefreshTokenRepository:
    return RefreshTokenRepository(db_session)


@pytest.fixture
def service(
    db_session: AsyncSession,
    users: UserRepository,
    refresh_tokens: RefreshTokenRepository,
) -> AuthService:
    return AuthService(db_session, users, refresh_tokens, get_settings())


@pytest.fixture
async def user(service: AuthService) -> User:
    return await service.register(
        UserCreate(email=LOGIN.email, password=LOGIN.password, phone=PHONE)
    )


async def test_register_creates_user_with_hashed_password(
    service: AuthService, users: UserRepository
) -> None:
    user = await service.register(
        UserCreate(
            email="Cristo@Test.com",
            password="contraseña123",
            display_name="Cristo",
            phone=PHONE,
        )
    )

    saved = await users.get_by_email("cristo@test.com")
    assert saved is not None
    assert saved.id == user.id
    assert saved.email == "cristo@test.com"
    assert saved.hashed_password != "contraseña123"
    assert await verify_password("contraseña123", saved.hashed_password)
    assert saved.role == UserRole.CUSTOMER
    assert saved.phone == PHONE


async def test_register_with_existing_email_fails_and_keeps_the_first_user(
    service: AuthService, users: UserRepository
) -> None:
    first = await service.register(
        UserCreate(email="cristo@test.com", password="contraseña123", phone=PHONE)
    )

    with pytest.raises(EmailAlreadyRegisteredError):
        await service.register(
            UserCreate(email="CRISTO@test.com", password="otra12345", phone=PHONE)
        )

    # El rollback solo deshace el registro fallido: el primero ya estaba confirmado.
    saved = await users.get_by_email("cristo@test.com")
    assert saved is not None
    assert saved.id == first.id


async def test_create_agent(service: AuthService, users: UserRepository) -> None:
    await service.create_agent(
        AgentCreate(email="Agente@Banco.com", password="contraseña123")
    )

    saved = await users.get_by_email("agente@banco.com")
    assert saved is not None
    assert saved.role == UserRole.AGENT
    assert saved.phone is None
    assert await verify_password("contraseña123", saved.hashed_password)


async def test_create_agent_with_existing_email_fails(
    service: AuthService, user: User
) -> None:
    with pytest.raises(EmailAlreadyRegisteredError):
        await service.create_agent(AgentCreate(email=user.email, password="otra12345"))


async def test_login_returns_tokens_and_stores_only_the_refresh_hash(
    service: AuthService, refresh_tokens: RefreshTokenRepository, user: User
) -> None:
    tokens = await service.login(LOGIN)

    assert await service.get_user_from_access_token(tokens.access_token) is user
    stored = await refresh_tokens.get_by_hash(hash_refresh_token(tokens.refresh_token))
    assert stored is not None
    assert stored.user_id == user.id
    assert stored.revoked_at is None
    assert stored.token_hash != tokens.refresh_token


@pytest.mark.parametrize(
    "data",
    [
        LoginRequest(email="cristo@test.com", password="incorrecta"),
        LoginRequest(email="nadie@test.com", password="contraseña123"),
    ],
    ids=["contraseña incorrecta", "email que no existe"],
)
async def test_login_with_wrong_credentials_fails(
    service: AuthService, user: User, data: LoginRequest
) -> None:
    with pytest.raises(InvalidCredentialsError):
        await service.login(data)


async def test_refresh_rotates_the_token(
    service: AuthService, refresh_tokens: RefreshTokenRepository, user: User
) -> None:
    first = await service.login(LOGIN)

    second = await service.refresh(first.refresh_token)

    assert second.refresh_token != first.refresh_token
    old = await refresh_tokens.get_by_hash(hash_refresh_token(first.refresh_token))
    assert old is not None
    assert old.revoked_at is not None
    # El nuevo sí sirve.
    await service.refresh(second.refresh_token)


async def test_reusing_a_rotated_token_revokes_every_token_of_that_user(
    service: AuthService, user: User
) -> None:
    other_user = await service.register(
        UserCreate(email="otra@test.com", password="contraseña123", phone=PHONE)
    )
    other_user_tokens = await service.login(
        LoginRequest(email=other_user.email, password="contraseña123")
    )
    stolen = await service.login(LOGIN)
    other_device = await service.login(LOGIN)
    rotated = await service.refresh(stolen.refresh_token)

    # Alguien usa la copia del token que ya se rotó.
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(stolen.refresh_token)

    # Todas las sesiones de ese usuario quedan revocadas...
    for tokens in (rotated, other_device):
        with pytest.raises(InvalidRefreshTokenError):
            await service.refresh(tokens.refresh_token)
    # ...pero las de los demás usuarios no.
    await service.refresh(other_user_tokens.refresh_token)


async def test_expired_refresh_token_fails(
    service: AuthService, refresh_tokens: RefreshTokenRepository, user: User
) -> None:
    tokens = await service.login(LOGIN)
    stored = await refresh_tokens.get_by_hash(hash_refresh_token(tokens.refresh_token))
    assert stored is not None
    stored.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(tokens.refresh_token)


async def test_unknown_refresh_token_fails(service: AuthService) -> None:
    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh("no-existe")


async def test_logout_revokes_the_refresh_token(
    service: AuthService, user: User
) -> None:
    tokens = await service.login(LOGIN)

    await service.logout(tokens.refresh_token)

    with pytest.raises(InvalidRefreshTokenError):
        await service.refresh(tokens.refresh_token)


async def test_logout_with_unknown_token_does_nothing(service: AuthService) -> None:
    await service.logout("no-existe")


async def test_invalid_access_token_fails(service: AuthService) -> None:
    with pytest.raises(NotAuthenticatedError):
        await service.get_user_from_access_token("no-es-un-jwt")


async def test_access_token_of_a_deleted_user_fails(
    service: AuthService, db_session: AsyncSession, user: User
) -> None:
    tokens = await service.login(LOGIN)
    await db_session.delete(user)
    await db_session.flush()

    with pytest.raises(NotAuthenticatedError):
        await service.get_user_from_access_token(tokens.access_token)
