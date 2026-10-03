import uuid

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from revision_rag.models import (
    Chunk,
    Conversation,
    Document,
    Flashcard,
    Message,
    Project,
    StudyMaterial,
    User,
)
from revision_rag.models.chunk import EMBEDDING_DIMENSIONS
from revision_rag.models.enums import MessageRole, SourceType, StudyMaterialKind


async def count(session: AsyncSession, model: type) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


def make_project(email: str = "user@test.com") -> Project:
    return Project(user=User(email=email, hashed_password="x"), name="AWS")


def make_document(project: Project) -> Document:
    document = Document(
        project=project,
        source_type=SourceType.MARKDOWN,
        original_filename="VPC.md",
        storage_key=f"{uuid.uuid4()}.md",
        size_bytes=1234,
        embedding_model="nomic-embed-text-v2-moe",
    )
    document.chunks.append(
        Chunk(
            chunk_index=0,
            content="Una VPC es una red virtual",
            token_count=6,
            embedding=[0.1] * EMBEDDING_DIMENSIONS,
        )
    )
    return document


# Se ejecuta dos veces con el mismo email: si el rollback entre tests no
# funcionara, la segunda vez fallaría por el email repetido.
@pytest.mark.parametrize("run", [1, 2])
async def test_each_test_starts_with_an_empty_database(
    db_session: AsyncSession, run: int
) -> None:
    assert await count(db_session, User) == 0

    db_session.add(User(email="isolation@test.com", hashed_password="x"))
    await db_session.flush()

    assert await count(db_session, User) == 1


async def test_email_is_unique_ignoring_case(db_session: AsyncSession) -> None:
    db_session.add(User(email="cristo@test.com", hashed_password="x"))
    await db_session.flush()

    db_session.add(User(email="Cristo@Test.com", hashed_password="x"))
    with pytest.raises(IntegrityError, match="uq_users_email_lower"):
        await db_session.flush()


async def test_deleting_a_document_cascades_to_its_chunks_and_materials(
    db_session: AsyncSession,
) -> None:
    project = make_project()
    document = make_document(project)
    material = StudyMaterial(
        project=project,
        document=document,
        kind=StudyMaterialKind.FLASHCARDS,
        title="Flashcards — VPC",
        model="gemma4:12b",
        prompt_version="v1",
    )
    material.flashcards.append(Flashcard(position=0, question="¿VPC?", answer="Red"))
    conversation = Conversation(project=project)
    conversation.messages.append(Message(role=MessageRole.USER, content="¿Qué es?"))
    db_session.add(project)
    await db_session.flush()

    await db_session.execute(delete(Document).where(Document.id == document.id))

    assert await count(db_session, Chunk) == 0
    assert await count(db_session, StudyMaterial) == 0
    assert await count(db_session, Flashcard) == 0
    # El historial del chat no depende del documento: se conserva.
    assert await count(db_session, Message) == 1


async def test_deleting_a_user_cascades_to_everything_they_own(
    db_session: AsyncSession,
) -> None:
    project = make_project()
    make_document(project)
    Conversation(project=project).messages.append(
        Message(role=MessageRole.USER, content="¿Qué es?")
    )
    db_session.add(project)
    await db_session.flush()

    await db_session.execute(delete(User))

    for model in (Project, Document, Chunk, Conversation, Message):
        assert await count(db_session, model) == 0
