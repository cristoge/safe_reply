import asyncio
import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from pwdlib import PasswordHash

# argon2id con los parámetros recomendados. El hash guarda algoritmo,
# parámetros y salt, así que basta con una columna de texto.
_password_hash = PasswordHash.recommended()

# Hash de una contraseña aleatoria que nadie conoce. El login lo comprueba
# cuando el email no existe, para tardar lo mismo que con un email que sí
# existe: si no, midiendo el tiempo de respuesta se sabría qué emails están
# registrados.
DUMMY_PASSWORD_HASH = _password_hash.hash(secrets.token_urlsafe(32))

JWT_ALGORITHM = "HS256"


# argon2 es lento a propósito (~30 ms de CPU) para que probar millones de
# contraseñas sea caro. Ese cálculo bloquearía el event loop, así que se hace
# en otro hilo: mientras tanto el servidor sigue atendiendo otras peticiones.
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_password_hash.hash, password)


async def verify_password(password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(_password_hash.verify, password, hashed_password)


def create_access_token(
    user_id: uuid.UUID, *, secret_key: str, expires_in: timedelta
) -> str:
    """JWT firmado con el id del usuario en "sub". Caduca solo: no se guarda en la BD."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "iat": now, "exp": now + expires_in}
    return jwt.encode(payload, secret_key, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str, *, secret_key: str) -> uuid.UUID:
    """Devuelve el id del usuario.

    Lanza jwt.InvalidTokenError si la firma no cuadra, ha caducado o le faltan
    campos. algorithms=[...] es obligatorio: sin él, un token con "alg": "none"
    (sin firma) se podría aceptar como válido.
    """
    payload = jwt.decode(
        token,
        secret_key,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    return uuid.UUID(payload["sub"])


def generate_refresh_token() -> str:
    # 32 bytes aleatorios (256 bits): imposible de adivinar. No es un JWT, solo
    # un valor opaco que se busca en la tabla refresh_tokens.
    return secrets.token_urlsafe(32)


def hash_refresh_token(token: str) -> str:
    # SHA-256 y no argon2: argon2 es lento para que no se puedan probar
    # contraseñas débiles por fuerza bruta, pero este token es aleatorio y no
    # se puede adivinar. Además, al ser determinista se puede buscar por él.
    return hashlib.sha256(token.encode()).hexdigest()
