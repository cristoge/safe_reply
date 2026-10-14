from collections.abc import Sequence

from fastapi import APIRouter

from revision_rag.api.deps import CurrentUser, Pagination, UserServiceDep
from revision_rag.core.exceptions import NotAuthenticatedError
from revision_rag.models import User
from revision_rag.schemas.user import UserRead, UserUpdate

router = APIRouter(
    prefix="/users",
    tags=["users"],
    responses={
        NotAuthenticatedError.status_code: {"description": NotAuthenticatedError.detail}
    },
)


@router.get("/me", response_model=UserRead)
async def read_me(user: CurrentUser) -> User:
    return user


@router.patch("/me", response_model=UserRead)
async def update_me(
    data: UserUpdate, user: CurrentUser, service: UserServiceDep
) -> User:
    return await service.update_profile(user, data)


# Solo en desarrollo: main.py no lo registra en producción. Con el registro
# abierto, cualquiera podría crearse una cuenta y ver los emails de todos.
dev_router = APIRouter(prefix="/users", tags=["dev"])


@dev_router.get("", response_model=list[UserRead])
async def list_users(service: UserServiceDep, pagination: Pagination) -> Sequence[User]:
    return await service.list_users(limit=pagination.limit, offset=pagination.offset)
