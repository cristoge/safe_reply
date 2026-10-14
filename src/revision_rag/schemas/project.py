import uuid
from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Se quitan los espacios de los lados antes de validar la longitud: "   " se
# queda en "" y falla con 422 (la BD también lo rechazaría, con el CHECK
# name_not_empty, pero con un error 500 en vez de un mensaje claro).
ProjectName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)
]
ProjectDescription = Annotated[str, Field(max_length=2000)]


class ProjectCreate(BaseModel):
    name: ProjectName
    description: ProjectDescription | None = None


class ProjectUpdate(BaseModel):
    name: ProjectName | None = None
    description: ProjectDescription | None = None

    @field_validator("name")
    @classmethod
    def name_cannot_be_null(cls, name: str | None) -> str | None:
        # Solo se ejecuta si "name" viene en el body: omitirlo es "no cambiar",
        # pero {"name": null} intentaría dejar el proyecto sin nombre.
        if name is None:
            raise ValueError("name no puede ser null")
        return name


class ProjectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
