from typing import Literal

from pydantic import BaseModel, EmailStr, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    # Sin min_length: en el login no se valida la política de contraseñas.
    password: str

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        return email.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    # Segundos hasta que caduca el access token: el cliente sabe cuándo refrescar.
    expires_in: int


class RefreshRequest(BaseModel):
    """Para /auth/refresh (pedir tokens nuevos) y /auth/logout (revocar)."""

    refresh_token: str
