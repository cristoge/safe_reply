from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Nombres consistentes para índices y restricciones. Sin esto, Postgres los
# inventa y Alembic no puede referirse a ellos de forma fiable en migraciones.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    # El valor de updated_at lo calcula Postgres (func.now()) en cada UPDATE.
    # Sin esto, SQLAlchemy no lo conoce y lo deja caducado: leerlo después del
    # commit intentaría recargarlo, y en async eso da MissingGreenlet. Con
    # eager_defaults lo recoge en el mismo UPDATE (RETURNING), sin otra consulta.
    __mapper_args__: ClassVar[dict[str, Any]] = {"eager_defaults": True}
