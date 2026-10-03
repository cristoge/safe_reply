import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class UserCreate(BaseModel):
    email: EmailStr
    # max_length: argon2 acepta contraseñas enormes, pero hashear megas de texto
    # es una forma fácil de saturar el servidor.
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=100)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, email: str) -> str:
        # EmailStr solo pasa a minúsculas el dominio; aquí el email entero, para
        # que "Cristo@test.com" y "cristo@test.com" sean el mismo usuario.
        return email.strip().lower()


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    created_at: datetime
