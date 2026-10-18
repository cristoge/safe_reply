import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin
from revision_rag.models.enums import UserRole, str_enum

if TYPE_CHECKING:
    from revision_rag.models.refresh_token import RefreshToken
    from revision_rag.models.ticket import Ticket


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(String(320))
    hashed_password: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(100))
    # server_default: los usuarios que ya existían al añadir la columna quedan
    # como clientes. Los agentes solo se crean por script, nunca por la API.
    role: Mapped[UserRole] = mapped_column(
        str_enum(UserRole, "user_role"),
        default=UserRole.CUSTOMER,
        server_default=UserRole.CUSTOMER.value,
    )
    # E.164 (+34600123456): como mucho 15 dígitos y el "+". Nullable porque los
    # agentes no lo necesitan; que los clientes lo den lo exige Pydantic al
    # registrarse.
    phone: Mapped[str | None] = mapped_column(String(16))

    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )
    # Solo los tickets que ha abierto como cliente. foreign_keys: tickets tiene
    # dos FK a users (cliente y agente asignado) y hay que decir cuál es esta.
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="customer",
        foreign_keys="Ticket.customer_id",
        lazy="raise",
        passive_deletes=True,
    )

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)
