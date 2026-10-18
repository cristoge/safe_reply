from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la app, leída de las variables de entorno y del `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Por defecto "production": si se olvida la variable, las rutas de
    # desarrollo no existen (falla del lado seguro).
    environment: Literal["development", "production"] = "production"

    database_url: str
    redis_url: str

    # SecretStr: al imprimir Settings (logs, errores) sale '**********'. El
    # valor real se lee con .get_secret_value(). Mínimo 32 bytes: es lo que
    # pide el RFC 7518 para HS256.
    jwt_secret_key: SecretStr = Field(min_length=32)
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7


@lru_cache
def get_settings() -> Settings:
    # Los campos los rellena pydantic-settings desde el entorno/.env; el type
    # checker no lo sabe y cree que falta database_url.
    return Settings()  # pyright: ignore[reportCallIssue]
