from enum import StrEnum

from sqlalchemy import Enum


class SourceType(StrEnum):
    PDF = "pdf"
    MARKDOWN = "markdown"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class MessageStatus(StrEnum):
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class StudyMaterialKind(StrEnum):
    SUMMARY = "summary"
    FLASHCARDS = "flashcards"
    QUIZ = "quiz"
    GLOSSARY = "glossary"


def str_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """Columna varchar + CHECK (no un tipo ENUM nativo de Postgres).

    Así añadir o quitar valores es una migración normal. Se guardan los valores
    ("pending"), no los nombres ("PENDING"), y el CHECK hay que pedirlo
    explícitamente: en SQLAlchemy 2 no se crea por defecto.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(len(member.value) for member in enum_cls),
        values_callable=lambda cls: [member.value for member in cls],
    )
