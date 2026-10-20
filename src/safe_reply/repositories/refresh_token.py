import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import RefreshToken


class RefreshTokenRepository:
    """Acceso a la tabla refresh_tokens. No hace commit(): eso lo decide el servicio."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, token: RefreshToken) -> RefreshToken:
        self.session.add(token)
        await self.session.flush()
        return token

    async def get_by_hash(
        self, token_hash: str, *, for_update: bool = False
    ) -> RefreshToken | None:
        stmt = select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        if for_update:
            # SELECT ... FOR UPDATE: bloquea la fila hasta el commit. Si llegan
            # dos /refresh a la vez con el mismo token, el segundo espera y
            # luego lo ve ya revocado, en vez de rotarlo los dos.
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def revoke_all_for_user(self, user_id: uuid.UUID) -> None:
        # Un solo UPDATE en la BD, sin cargar los tokens en Python. La hora sale
        # de Python y no de func.now(): así SQLAlchemy también actualiza los
        # tokens que ya tiene cargados en la sesión, sin tener que recargarlos
        # (en async, esa recarga implícita da error).
        stmt = (
            update(RefreshToken)
            .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
        await self.session.execute(stmt)
