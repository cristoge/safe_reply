from fastapi import APIRouter, status

from revision_rag.api.deps import AuthServiceDep
from revision_rag.core.exceptions import EmailAlreadyRegisteredError
from revision_rag.models import User
from revision_rag.schemas.user import UserCreate, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    # Se devuelve el User de SQLAlchemy y response_model lo convierte en
    # UserRead: solo salen sus campos, nunca hashed_password.
    response_model=UserRead,
    responses={
        EmailAlreadyRegisteredError.status_code: {
            "description": EmailAlreadyRegisteredError.detail
        }
    },
)
async def register(data: UserCreate, service: AuthServiceDep) -> User:
    return await service.register(data)
