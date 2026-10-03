from collections.abc import Sequence

from revision_rag.models import User
from revision_rag.repositories.user import UserRepository


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self.users = users

    async def list_users(self, *, limit: int, offset: int) -> Sequence[User]:
        # Ahora mismo solo delega en el repositorio. Aquí irán las reglas
        # (p. ej. "solo un administrador puede listar usuarios") cuando existan.
        return await self.users.list_all(limit=limit, offset=offset)
