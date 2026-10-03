import uuid
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from revision_rag.db.base import Base

if TYPE_CHECKING:
    from revision_rag.models.study_material import StudyMaterial


class Flashcard(Base):
    """v1: solo para leer. El progreso estilo Anki (SM-2) llegará con otra migración."""

    __tablename__ = "flashcards"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid7)
    # Sin index=True: lo cubre el UniqueConstraint (study_material_id, position).
    study_material_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("study_materials.id", ondelete="CASCADE")
    )
    position: Mapped[int]
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)

    study_material: Mapped[StudyMaterial] = relationship(
        back_populates="flashcards", lazy="raise"
    )

    __table_args__ = (
        UniqueConstraint("study_material_id", "position"),
        CheckConstraint("position >= 0", name="position_non_negative"),
    )
