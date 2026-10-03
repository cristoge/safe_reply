# Importar aquí todos los modelos: Alembic los descubre a través de este módulo.
from revision_rag.models.chunk import Chunk
from revision_rag.models.conversation import Conversation
from revision_rag.models.document import Document
from revision_rag.models.flashcard import Flashcard
from revision_rag.models.message import Message
from revision_rag.models.project import Project
from revision_rag.models.refresh_token import RefreshToken
from revision_rag.models.study_material import StudyMaterial
from revision_rag.models.user import User

__all__ = [
    "Chunk",
    "Conversation",
    "Document",
    "Flashcard",
    "Message",
    "Project",
    "RefreshToken",
    "StudyMaterial",
    "User",
]
