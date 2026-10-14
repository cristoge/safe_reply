import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from revision_rag.models.enums import DocumentStatus, SourceType


class DocumentRead(BaseModel):
    """Sin storage_key ni embedding_model: son detalles internos."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    source_type: SourceType
    size_bytes: int
    # None hasta que la ingesta lea el PDF (y siempre None en un .md).
    page_count: int | None
    status: DocumentStatus
    error_message: str | None
    created_at: datetime
    updated_at: datetime
