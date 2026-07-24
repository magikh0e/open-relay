"""Auth: registration throttling, and token revocation on password change."""
import pytest

from app.redis_client import redis_client
from tests.conftest import unique

pytestmark = pytest.mark.asyncio


async def test_login_returns_usable_token(client, alice):
    res = await client.post(
        "/auth/login",
        json={"username_or_email": alice["username"], "password": "password123"},
    )
    assert res.status_code == 200
    token = res.json()["access_token"]
    me = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert me.status_code == 200
    assert me.json()["username"] == alice["username"]


async def test_registration_is_rate_limited(client):
    """Signup is open, so an unthrottled endpoint lets one script mint
    unlimited accounts."""
    for key in await redis_client.keys("register:ip:*"):
        await redis_client.delete(key)

    codes = []
    for _ in range(8):
        name = unique()
        res = await client.post(
            "/auth/register",
            json={
                "username": name,
                "email": f"{name}@example.com",
                "password": "password123",
            },
        )
        codes.append(res.status_code)

    assert 429 in codes, f"never throttled: {codes}"
    # Cleanup so later tests can still register.
    for key in await redis_client.keys("register:ip:*"):
        await redis_client.delete(key)


async def test_password_change_revokes_other_sessions(client, user_factory):
    """The whole point of token_version: an old token must stop working."""
    user = await user_factory()
    old_headers = user["headers"]
    old_refresh = user["refresh"]

    # Old token works before the change.
    assert (await client.get("/users/me", headers=old_headers)).status_code == 200

    res = await client.post(
        "/users/me/password",
        headers=old_headers,
        json={"current_password": "password123", "new_password": "a-new-password"},
    )
    assert res.status_code == 200, res.text
    fresh = res.json()

    # The old access token is now rejected...
    assert (await client.get("/users/me", headers=old_headers)).status_code == 401
    # ...and the old refresh token can't mint a new one either.
    replay = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401

    # The caller keeps working via the pair returned by the change.
    new_headers = {"Authorization": f"Bearer {fresh['access_token']}"}
    assert (await client.get("/users/me", headers=new_headers)).status_code == 200


async def test_password_change_requires_current_password(client, alice):
    res = await client.post(
        "/users/me/password",
        headers=alice["headers"],
        json={"current_password": "wrong", "new_password": "something-else"},
    )
    assert res.status_code == 403


async def test_refresh_rotates_and_still_works(client, alice):
    res = await client.post("/auth/refresh", json={"refresh_token": alice["refresh"]})
    assert res.status_code == 200
    new_access = res.json()["access_token"]
    me = await client.get(
        "/users/me", headers={"Authorization": f"Bearer {new_access}"}
    )
    assert me.status_code == 200
