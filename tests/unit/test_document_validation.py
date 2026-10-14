import pytest

from revision_rag.core.exceptions import InvalidFileError, UnsupportedFileTypeError
from revision_rag.models.enums import SourceType
from revision_rag.services.document import clean_filename, detect_source_type


@pytest.mark.parametrize(
    ("filename", "content", "expected"),
    [
        ("apuntes.pdf", b"%PDF-1.7\n...", SourceType.PDF),
        ("APUNTES.PDF", b"%PDF-1.7\n...", SourceType.PDF),
        ("con-basura-delante.pdf", b"\n\n%PDF-1.4\n...", SourceType.PDF),
        ("notas.md", "# Título con tildes y ñ".encode(), SourceType.MARKDOWN),
    ],
)
def test_detect_valid_files(
    filename: str, content: bytes, expected: SourceType
) -> None:
    assert detect_source_type(filename, content) is expected


@pytest.mark.parametrize("filename", ["notas.txt", "foto.png", "sin-extension"])
def test_detect_unsupported_extension(filename: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_source_type(filename, b"lo que sea")


def test_detect_text_renamed_to_pdf() -> None:
    with pytest.raises(InvalidFileError, match="no es un PDF válido"):
        detect_source_type("falso.pdf", b"esto es texto, no un PDF")


def test_detect_markdown_that_is_not_utf8() -> None:
    with pytest.raises(InvalidFileError, match="UTF-8"):
        detect_source_type("notas.md", "ñandú".encode("latin-1"))


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("notas.md", "notas.md"),
        ("C:\\fakepath\\notas.md", "notas.md"),
        ("carpeta/notas.md", "notas.md"),
        ("x" * 300 + ".md", "x" * 255),
    ],
)
def test_clean_filename(filename: str, expected: str) -> None:
    assert clean_filename(filename) == expected
