from fastapi import APIRouter, status

from safe_reply.api.deps import AuthServiceDep
from safe_reply.core.exceptions import (
    EmailAlreadyRegisteredError,
    InvalidCredentialsError,
    InvalidRefreshTokenError,
)
from safe_reply.models import User
from safe_reply.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from safe_reply.schemas.user import UserCreate, UserRead

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


@router.post(
    "/login",
    responses={
        InvalidCredentialsError.status_code: {
            "description": InvalidCredentialsError.detail
        }
    },
)
async def login(data: LoginRequest, service: AuthServiceDep) -> TokenResponse:
    return await service.login(data)


@router.post(
    "/refresh",
    responses={
        InvalidRefreshTokenError.status_code: {
            "description": InvalidRefreshTokenError.detail
        }
    },
)
async def refresh(data: RefreshRequest, service: AuthServiceDep) -> TokenResponse:
    return await service.refresh(data.refresh_token)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(data: RefreshRequest, service: AuthServiceDep) -> None:
    await service.logout(data.refresh_token)
