from pydantic import BaseModel, Field


class PaginationParams(BaseModel):
    """?limit=...&offset=... de todas las rutas que devuelven listas.

    Definido una sola vez para que todas paginen igual.
    """

    limit: int = Field(default=5, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
