from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.core.exceptions import EmailAlreadyRegisteredError
from revision_rag.core.security import hash_password
from revision_rag.models import User
from revision_rag.repositories.user import UserRepository
from revision_rag.schemas.user import UserCreate


class AuthService:
    def __init__(self, session: AsyncSession, users: UserRepository) -> None:
        # La sesión se recibe para poder hacer commit(): el servicio es quien
        # sabe cuándo ha terminado una operación completa.
        self.session = session
        self.users = users

    async def register(self, data: UserCreate) -> User:
        user = User(
            email=data.email,
            hashed_password=await hash_password(data.password),
            display_name=data.display_name,
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
