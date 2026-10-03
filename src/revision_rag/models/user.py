import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from revision_rag.models.project import Project
    from revision_rag.models.refresh_token import RefreshToken


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    email: Mapped[str] = mapped_column(String(320))
    hashed_password: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(100))

    projects: Mapped[list[Project]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        back_populates="user", lazy="raise", passive_deletes=True
    )

    __table_args__ = (Index("uq_users_email_lower", func.lower(email), unique=True),)
