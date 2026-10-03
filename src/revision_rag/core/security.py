import asyncio

from pwdlib import PasswordHash

# argon2id con los parámetros recomendados. El hash guarda algoritmo,
# parámetros y salt, así que basta con una columna de texto.
_password_hash = PasswordHash.recommended()


# argon2 es lento a propósito (~30 ms de CPU) para que probar millones de
# contraseñas sea caro. Ese cálculo bloquearía el event loop, así que se hace
# en otro hilo: mientras tanto el servidor sigue atendiendo otras peticiones.
async def hash_password(password: str) -> str:
    return await asyncio.to_thread(_password_hash.hash, password)


async def verify_password(password: str, hashed_password: str) -> bool:
    return await asyncio.to_thread(_password_hash.verify, password, hashed_password)
