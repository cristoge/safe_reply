from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import User
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.user import UserUpdate


class UserService:
    def __init__(self, session: AsyncSession, users: UserRepository) -> None:
        self.session = session
        self.users = users

    async def list_users(self, *, limit: int, offset: int) -> Sequence[User]:
        return await self.users.list_all(limit=limit, offset=offset)

    async def update_profile(self, user: User, data: UserUpdate) -> User:
        # exclude_unset: solo los campos que vienen en el body (semántica de
        # PATCH). Así {"display_name": null} lo borra y {} no cambia nada.
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(user, field, value)
        await self.session.commit()
        return user
