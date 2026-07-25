"""Privacy preferences must be enforced by the server, not just hidden in the UI."""
from datetime import datetime, timezone

import pytest
from sqlalchemy import update

from app.database import SessionLocal
from app.models import User

pytestmark = pytest.mark.asyncio


async def test_defaults_are_permissive(client, alice):
    res = await client.get("/users/me/settings", headers=alice["headers"])
    assert res.json() == {
        "share_typing": True,
        "share_presence": True,
        "allow_dms": True,
        "discoverable": True,
        "share_last_active": True,
    }


async def _seed_last_active(user_id):
    async with SessionLocal() as db:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_active_at=datetime.now(timezone.utc))
        )
        await db.commit()


async def test_last_active_hidden_when_opted_out(client, alice, bob):
    # WS isn't exercised in these tests, so seed the timestamp directly.
    await _seed_last_active(bob["id"])

    # Shared by default: another user sees it.
    p = (await client.get(f"/users/{bob['id']}", headers=alice["headers"])).json()
    assert p["last_active_at"] is not None

    await client.patch(
        "/users/me/settings",
        headers=bob["headers"],
        json={"share_last_active": False},
    )
    # Hidden from others...
    p = (await client.get(f"/users/{bob['id']}", headers=alice["headers"])).json()
    assert p["last_active_at"] is None
    # ...but bob still sees his own (it was recorded regardless).
    own = (await client.get(f"/users/{bob['id']}", headers=bob["headers"])).json()
    assert own["last_active_at"] is not None


async def test_undiscoverable_user_is_hidden_from_search(client, alice, bob):
    found = await client.get(
        f"/users/search?q={bob['username']}", headers=alice["headers"]
    )
    assert [u["username"] for u in found.json()] == [bob["username"]]

    await client.patch(
        "/users/me/settings", headers=bob["headers"], json={"discoverable": False}
    )
    found = await client.get(
        f"/users/search?q={bob['username']}", headers=alice["headers"]
    )
    assert found.json() == []


async def test_allow_dms_blocks_new_conversations_only(client, alice, bob):
    await client.patch(
        "/users/me/settings", headers=bob["headers"], json={"allow_dms": False}
    )
    blocked = await client.post(
        "/dms", headers=alice["headers"], json={"user_id": bob["id"]}
    )
    assert blocked.status_code == 403

    # Re-allow, open it, then block again: the existing DM must keep working.
    await client.patch(
        "/users/me/settings", headers=bob["headers"], json={"allow_dms": True}
    )
    opened = await client.post(
        "/dms", headers=alice["headers"], json={"user_id": bob["id"]}
    )
    assert opened.status_code == 201
    await client.patch(
        "/users/me/settings", headers=bob["headers"], json={"allow_dms": False}
    )
    again = await client.post(
        "/dms", headers=alice["headers"], json={"user_id": bob["id"]}
    )
    assert again.status_code == 200 or again.status_code == 201


async def test_settings_are_partial_updates(client, alice):
    await client.patch(
        "/users/me/settings", headers=alice["headers"], json={"share_typing": False}
    )
    res = await client.get("/users/me/settings", headers=alice["headers"])
    body = res.json()
    assert body["share_typing"] is False
    # Untouched fields must not be reset.
    assert body["share_presence"] is True
    assert body["discoverable"] is True
