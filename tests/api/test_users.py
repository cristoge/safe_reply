import pytest
from httpx import AsyncClient

USERS_URL = "/api/v1/users"


async def register(client: AsyncClient, email: str) -> None:
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "contraseña123"}
    )
    assert response.status_code == 201


async def test_list_users_empty(client: AsyncClient) -> None:
    response = await client.get(USERS_URL)

    assert response.status_code == 200
    assert response.json() == []


async def test_list_users_returns_them_in_creation_order_without_hash(
    client: AsyncClient,
) -> None:
    for email in ("a@test.com", "b@test.com", "c@test.com"):
        await register(client, email)

    response = await client.get(USERS_URL)

    assert response.status_code == 200
    assert [user["email"] for user in response.json()] == [
        "a@test.com",
        "b@test.com",
        "c@test.com",
    ]
    assert "hashed_password" not in response.text


async def test_list_users_paginates(client: AsyncClient) -> None:
    for email in ("a@test.com", "b@test.com", "c@test.com"):
        await register(client, email)

    response = await client.get(USERS_URL, params={"limit": 2, "offset": 1})

    assert [user["email"] for user in response.json()] == ["b@test.com", "c@test.com"]


@pytest.mark.parametrize(
    "params",
    [{"limit": 0}, {"limit": 101}, {"offset": -1}],
    ids=["limit 0", "limit > 100", "offset negativo"],
)
async def test_list_users_with_invalid_pagination_returns_422(
    client: AsyncClient, params: dict[str, int]
) -> None:
    response = await client.get(USERS_URL, params=params)

    assert response.status_code == 422
