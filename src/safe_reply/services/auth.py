import uuid
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.core.config import Settings
from safe_reply.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    NotAuthenticatedError,
)
from safe_reply.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from safe_reply.models import RefreshToken, User
from safe_reply.models.enums import UserRole
from safe_reply.repositories.refresh_token import RefreshTokenRepository
from safe_reply.repositories.user import UserRepository
from safe_reply.schemas.auth import LoginRequest, TokenResponse
from safe_reply.schemas.user import AgentCreate, UserCreate


class AuthService:
    def __init__(
        self,
        session: AsyncSession,
        users: UserRepository,
        refresh_tokens: RefreshTokenRepository,
        settings: Settings,
    ) -> None:
        # La sesión se recibe para poder hacer commit(): el servicio es quien
        # sabe cuándo ha terminado una operación completa.
        self.session = session
        self.users = users
        self.refresh_tokens = refresh_tokens
        self.settings = settings

    async def register(self, data: UserCreate) -> User:
        # Siempre cliente: el rol no viene en el body (UserCreate no lo tiene),
        # así nadie puede darse permisos de agente registrándose.
        return await self._create_user(data, role=UserRole.CUSTOMER, phone=data.phone)

    async def create_agent(self, data: AgentCreate) -> User:
        """Solo lo llama scripts/create_agent.py. Ninguna ruta de la API lo expone."""
        return await self._create_user(data, role=UserRole.AGENT, phone=None)

    async def _create_user(
        self, data: AgentCreate | UserCreate, *, role: UserRole, phone: str | None
    ) -> User:
        user = User(
            email=data.email,
            hashed_password=await hash_password(data.password),
            display_name=data.display_name,
            role=role,
            phone=phone,
        )
        try:
            await self.users.add(user)
        except IntegrityError as exc:
            # El único UNIQUE de users es el email. Confiar en la base de datos
            # (y no en un "SELECT para ver si existe" antes) evita que dos
            # registros simultáneos con el mismo email pasen los dos.
            await self.session.rollback()
            raise EmailAlreadyRegisteredError from exc

        await self.session.commit()
        return user

    async def login(self, data: LoginRequest) -> TokenResponse:
        user = await self.users.get_by_email(data.email)
        if user is None:
            # Se verifica igualmente (contra un hash que no coincide nunca)
            # para tardar lo mismo que si el email existiera.
            await verify_password(data.password, DUMMY_PASSWORD_HASH)
            raise InvalidCredentialsError
        if not await verify_password(data.password, user.hashed_password):
            raise InvalidCredentialsError

        tokens = await self._issue_tokens(user.id)
        await self.session.commit()
        return tokens

    async def refresh(self, refresh_token: str) -> TokenResponse:
        """Rotación: el refresh token usado se revoca y se emite uno nuevo."""
        token = await self.refresh_tokens.get_by_hash(
            hash_refresh_token(refresh_token), for_update=True
        )
        if token is None:
            raise InvalidRefreshTokenError
        if token.revoked_at is not None:
            # Un token ya rotado solo puede llegar si alguien tiene una copia
            # (robo). No se sabe si es el usuario o el ladrón, así que se
            # revocan todos los del usuario: los dos tendrán que hacer login.
            await self.refresh_tokens.revoke_all_for_user(token.user_id)
            await self.session.commit()
            raise InvalidRefreshTokenError
        if token.expires_at <= datetime.now(UTC):
            raise InvalidRefreshTokenError

        token.revoked_at = datetime.now(UTC)
        tokens = await self._issue_tokens(token.user_id)
        await self.session.commit()
        return tokens

    async def logout(self, refresh_token: str) -> None:
        # Sin error si el token no existe o ya estaba revocado: el resultado
        # que se pide (que ese token no sirva) ya se cumple.
        token = await self.refresh_tokens.get_by_hash(hash_refresh_token(refresh_token))
        if token is not None and token.revoked_at is None:
            token.revoked_at = datetime.now(UTC)
            await self.session.commit()

    async def get_user_from_access_token(self, access_token: str) -> User:
        try:
            user_id = decode_access_token(
                access_token, secret_key=self.settings.jwt_secret_key.get_secret_value()
            )
        except jwt.InvalidTokenError as exc:
            raise NotAuthenticatedError from exc

        # El token puede ser válido y el usuario ya no existir (cuenta borrada).
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise NotAuthenticatedError
        return user

    async def _issue_tokens(self, user_id: uuid.UUID) -> TokenResponse:
        """Crea el par de tokens. El refresh se guarda hasheado; el access no se guarda."""
        refresh_token = generate_refresh_token()
        await self.refresh_tokens.add(
            RefreshToken(
                user_id=user_id,
                token_hash=hash_refresh_token(refresh_token),
                expires_at=datetime.now(UTC)
                + timedelta(days=self.settings.refresh_token_expire_days),
            )
        )
        access_expires_in = timedelta(minutes=self.settings.access_token_expire_minutes)
        access_token = create_access_token(
            user_id,
            secret_key=self.settings.jwt_secret_key.get_secret_value(),
            expires_in=access_expires_in,
        )
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=int(access_expires_in.total_seconds()),
        )
