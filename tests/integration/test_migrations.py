from alembic import command
from alembic.config import Config


def test_models_and_migrations_are_in_sync(alembic_config: Config) -> None:
    # Igual que `alembic check`: falla si algún modelo ha cambiado y falta
    # generar su migración.
    command.check(alembic_config)
