from pathlib import Path

import pytest

from revision_rag.clients.storage import LocalStorage


async def test_save_and_delete(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path)

    await storage.save("projects/p1/doc.md", b"# Hola")
    assert (tmp_path / "projects/p1/doc.md").read_bytes() == b"# Hola"

    await storage.delete("projects/p1/doc.md")
    assert not (tmp_path / "projects/p1/doc.md").exists()


async def test_delete_a_missing_file_does_nothing(tmp_path: Path) -> None:
    await LocalStorage(tmp_path).delete("no-existe.pdf")


async def test_key_cannot_escape_the_storage_folder(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "storage")

    with pytest.raises(ValueError, match="fuera del storage"):
        await storage.save("../fuera.txt", b"x")
    assert not (tmp_path / "fuera.txt").exists()
