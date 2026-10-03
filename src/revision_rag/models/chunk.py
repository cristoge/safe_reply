import uuid
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base

if TYPE_CHECKING:
    from revision_rag.models.document import Document

# Dimensión nativa de nomic-embed-text-v2-moe. Cambiarla exige una migración
# y reprocesar todos los documentos.
EMBEDDING_DIMENSIONS = 768


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # Sin index=True: el UniqueConstraint (document_id, chunk_index) ya crea un
    # índice que empieza por document_id y sirve para buscar por él.
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE")
    )
    chunk_index: Mapped[int]
    content: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int]
    # PDF: página de origen. Markdown: ruta de títulos ("SOLID › S — ...").
    page_number: Mapped[int | None]
    section: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    document: Mapped[Document] = relationship(back_populates="chunks", lazy="raise")

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index"),
        CheckConstraint("chunk_index >= 0", name="chunk_index_non_negative"),
    )
