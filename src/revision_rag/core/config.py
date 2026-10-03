from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuración de la app, leída de las variables de entorno y del `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str


@lru_cache
def get_settings() -> Settings:
    # Los campos los rellena pydantic-settings desde el entorno/.env; el type
    # checker no lo sabe y cree que falta database_url.
    return Settings()  # pyright: ignore[reportCallIssue]
