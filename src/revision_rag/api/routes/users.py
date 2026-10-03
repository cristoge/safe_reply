from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Query

from revision_rag.api.deps import UserServiceDep
from revision_rag.models import User
from revision_rag.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


# TODO(seguridad): sin autenticación, cualquiera puede ver los emails de todos
# los usuarios. Solo para desarrollo: cuando exista el login, protegerlo
# (solo administradores) o quitarlo.
@router.get("", response_model=list[UserRead])
async def list_users(
    service: UserServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Sequence[User]:
    return await service.list_users(limit=limit, offset=offset)
