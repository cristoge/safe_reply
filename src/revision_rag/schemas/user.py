import re
import uuid
from datetime import datetime
from typing import Annotated

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
)

from revision_rag.models.enums import UserRole

# E.164: "+", el prefijo del país (no empieza por 0) y como mucho 15 dígitos en
# total. No comprueba que el número exista (eso pediría la librería
# phonenumbers); para un banco ficticio basta con el formato.
E164_PATTERN = re.compile(r"\+[1-9]\d{6,14}")


def _strip_separators(value: object) -> object:
    # Se acepta "+34 600-123-456" tal como lo escribe la gente; se guarda sin
    # separadores, para que el mismo número no aparezca de dos formas.
    return re.sub(r"[\s\-().]", "", value) if isinstance(value, str) else value


def _check_e164(value: str) -> str:
    if not E164_PATTERN.fullmatch(value):
        raise ValueError("el teléfono debe estar en formato E.164, p. ej. +34600123456")
    return value


PhoneNumber = Annotated[
    str, BeforeValidator(_strip_separators), AfterValidator(_check_e164)
]


class _NewUser(BaseModel):
    """Lo común a crear un cliente (registro) y un agente (script)."""

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


class UserCreate(_NewUser):
    """Registro por la API: siempre crea un cliente (no hay campo role)."""

    # Obligatorio: si el ticket va a una persona, el banco llama al cliente.
    phone: PhoneNumber


class AgentCreate(_NewUser):
    """Solo para scripts/create_agent.py: ninguna ruta de la API lo usa."""


class UserUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=100)


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str
    display_name: str | None
    role: UserRole
    phone: str | None
    created_at: datetime
