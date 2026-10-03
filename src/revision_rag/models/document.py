import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, TimestampMixin
from revision_rag.models.enums import DocumentStatus, SourceType, str_enum

if TYPE_CHECKING:
    from revision_rag.models.chunk import Chunk
    from revision_rag.models.project import Project
    from revision_rag.models.study_material import StudyMaterial


class Document(TimestampMixin, Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"))
    original_filename: Mapped[str] = mapped_column(String(255))
    # Ruta en el storage, generada con un uuid (nunca el nombre original).
    storage_key: Mapped[str] = mapped_column(String(500), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    page_count: Mapped[int | None]
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, "document_status"),
        default=DocumentStatus.PENDING,
        server_default=DocumentStatus.PENDING.value,
    )
    error_message: Mapped[str | None] = mapped_column(Text)
    embedding_model: Mapped[str] = mapped_column(String(100))

    project: Mapped[Project] = relationship(back_populates="documents", lazy="raise")
    chunks: Mapped[list[Chunk]] = relationship(
        back_populates="document", lazy="raise", passive_deletes=True
    )
    study_materials: Mapped[list[StudyMaterial]] = relationship(
        back_populates="document", lazy="raise", passive_deletes=True
    )

    __table_args__ = (CheckConstraint("size_bytes > 0", name="size_bytes_positive"),)
