from revision_rag.core.security import hash_password, verify_password


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
