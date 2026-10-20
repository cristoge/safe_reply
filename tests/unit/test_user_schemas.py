import pytest
from pydantic import ValidationError

from safe_reply.schemas.user import UserCreate


def make_user(phone: str) -> UserCreate:
    return UserCreate(email="cristo@test.com", password="contraseña123", phone=phone)


@pytest.mark.parametrize(
    ("phone", "stored"),
    [
        ("+34600123456", "+34600123456"),
        ("+1 (415) 555-2671", "+14155552671"),
        ("+44 20 7946 0958", "+442079460958"),
        ("+34 600-123-456", "+34600123456"),
    ],
)
def test_valid_phones_are_stored_without_separators(phone: str, stored: str) -> None:
    assert make_user(phone).phone == stored


@pytest.mark.parametrize(
    "phone",
    [
        "600123456",  # sin prefijo de país
        "0034600123456",  # 00 en vez de +
        "+0600123456",  # el prefijo no empieza por 0
        "+34600",  # demasiado corto
        "+3460012345678901",  # más de 15 dígitos
        "+34 600 ABC 456",  # letras
        "",
    ],
)
def test_invalid_phones_are_rejected(phone: str) -> None:
    with pytest.raises(ValidationError, match="E.164"):
        make_user(phone)
