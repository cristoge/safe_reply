import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from safe_reply.models import User


class UserRepository:
    """Acceso a la tabla users. No hace commit(): eso lo decide el servicio."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        # Comparar con lower(email) hace que Postgres use el índice único
        # uq_users_email_lower. Con User.email == email no lo usaría.
        stmt = select(User).where(func.lower(User.email) == email.lower())
        return await self.session.scalar(stmt)

    async def list_all(self, *, limit: int, offset: int) -> Sequence[User]:
        # Orden fijo: sin ORDER BY, Postgres no garantiza el orden y la
        # paginación podría repetir o saltarse filas entre páginas.
        stmt = (
            select(User).order_by(User.created_at, User.id).limit(limit).offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def add(self, user: User) -> User:
        self.session.add(user)
        # flush() envía el INSERT sin confirmar la transacción: así se obtienen
        # los valores del servidor (created_at) y, si el email ya existe, el
        # error salta aquí y no más tarde en el commit().
        await self.session.flush()
        return user
