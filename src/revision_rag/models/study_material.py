import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base, CreatedAtMixin
from revision_rag.models.enums import StudyMaterialKind, str_enum

if TYPE_CHECKING:
    from revision_rag.models.document import Document
    from revision_rag.models.flashcard import Flashcard
    from revision_rag.models.project import Project


class StudyMaterial(CreatedAtMixin, Base):
    __tablename__ = "study_materials"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    # None = generado a partir del proyecto entero.
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[StudyMaterialKind] = mapped_column(
        str_enum(StudyMaterialKind, "study_material_kind")
    )
    title: Mapped[str] = mapped_column(String(200))
    # Forma según el kind (validada con Pydantic). Vacío en flashcards: van en su tabla.
    content: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(50))

    project: Mapped[Project] = relationship(
        back_populates="study_materials", lazy="raise"
    )
    document: Mapped[Document | None] = relationship(
        back_populates="study_materials", lazy="raise"
    )
    flashcards: Mapped[list[Flashcard]] = relationship(
        back_populates="study_material", lazy="raise", passive_deletes=True
    )
