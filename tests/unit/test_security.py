import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from safe_reply.core.security import (
    create_access_token,
    decode_access_token,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)

SECRET_KEY = "clave-solo-para-tests-de-al-menos-32-bytes"


async def test_hash_is_not_the_password_and_uses_argon2() -> None:
    hashed = await hash_password("contraseña123")

    assert hashed != "contraseña123"
    assert hashed.startswith("$argon2id$")


async def test_same_password_gives_different_hashes() -> None:
    # Cada hash lleva un salt aleatorio: dos usuarios con la misma contraseña
    # no tienen el mismo hash.
    assert await hash_password("contraseña123") != await hash_password("contraseña123")


async def test_verify_password() -> None:
    hashed = await hash_password("contraseña123")

    assert await verify_password("contraseña123", hashed) is True
    assert await verify_password("otra-cosa", hashed) is False


def test_access_token_gives_back_the_user_id() -> None:
    user_id = uuid.uuid7()
    token = create_access_token(
        user_id, secret_key=SECRET_KEY, expires_in=timedelta(minutes=5)
    )

    assert decode_access_token(token, secret_key=SECRET_KEY) == user_id


def test_expired_access_token_is_rejected() -> None:
    token = create_access_token(
        uuid.uuid7(), secret_key=SECRET_KEY, expires_in=timedelta(seconds=-1)
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(token, secret_key=SECRET_KEY)


def test_access_token_signed_with_another_key_is_rejected() -> None:
    token = create_access_token(
        uuid.uuid7(),
        secret_key="otra-clave-distinta-de-al-menos-32-bytes",
        expires_in=timedelta(minutes=5),
    )

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(token, secret_key=SECRET_KEY)


def test_unsigned_access_token_is_rejected() -> None:
    # "alg": "none" = token sin firma. Si se aceptara, cualquiera podría
    # fabricarse uno con el id de otro usuario.
    payload = {
        "sub": str(uuid.uuid7()),
        "exp": datetime.now(UTC) + timedelta(minutes=5),
    }
    token = jwt.encode(payload, key=None, algorithm="none")

    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(token, secret_key=SECRET_KEY)


def test_refresh_tokens_are_random() -> None:
    assert generate_refresh_token() != generate_refresh_token()


def test_refresh_token_hash_is_deterministic_and_fits_the_column() -> None:
    token = generate_refresh_token()

    # Determinista: el mismo token da el mismo hash, así se puede buscar por él.
    assert hash_refresh_token(token) == hash_refresh_token(token)
    assert hash_refresh_token(token) != token
    # refresh_tokens.token_hash es String(64): SHA-256 en hexadecimal.
    assert len(hash_refresh_token(token)) == 64
