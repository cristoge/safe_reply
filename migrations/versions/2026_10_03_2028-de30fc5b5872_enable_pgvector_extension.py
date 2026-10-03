"""enable pgvector extension

Revision ID: de30fc5b5872
Revises:
Create Date: 2026-10-03 20:28:17.319539

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "de30fc5b5872"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # La imagen pgvector/pgvector trae la extensión instalada; aquí solo se activa
    # en esta base de datos. Autogenerate no detecta extensiones: va a mano.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP EXTENSION IF EXISTS vector")
