"""Password-protected (IRC +k) public channels: set, join, bypass, remove."""
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.database import SessionLocal
from app.models import User

pytestmark = pytest.mark.asyncio

KEY = "letmein-please"


async def _make_admin(user_id):
    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(is_admin=True)
        )
        await db.commit()


async def _new_channel(client, owner, **body):
    slug = "pw" + uuid4().hex[:8]
    payload = {"slug": slug, "name": slug, **body}
    r = await client.post("/channels", json=payload, headers=owner["headers"])
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_with_password_flags_has_password(client, alice):
    ch = await _new_channel(client, alice, password=KEY)
    assert ch["has_password"] is True
    # The hash itself is never exposed.
    assert "password_hash" not in ch and "password" not in ch


async def _channel_texts(client, headers, cid):
    r = await client.get(f"/channels/{cid}/messages", headers=headers)
    assert r.status_code == 200, r.text
    return [m["content"] for m in r.json()]


async def test_password_notice_is_redacted(client, alice):
    ch = await _new_channel(client, alice)  # owner + member, no key yet
    await client.patch(
        f"/channels/{ch['id']}", json={"password": KEY}, headers=alice["headers"]
    )
    texts = await _channel_texts(client, alice["headers"], ch["id"])
    # Members see that a key was set...
    assert any("set a channel password" in t for t in texts)
    # ...but the key itself is never posted.
    assert not any(KEY in t for t in texts)


async def test_join_and_leave_post_notices(client, alice, bob):
    ch = await _new_channel(client, alice)
    await client.post(f"/channels/{ch['id']}/join", headers=bob["headers"])
    await client.post(f"/channels/{ch['id']}/leave", headers=bob["headers"])
    texts = await _channel_texts(client, alice["headers"], ch["id"])
    assert any("joined the channel" in t for t in texts)
    assert any("left the channel" in t for t in texts)


async def test_join_requires_correct_password(client, alice, bob):
    ch = await _new_channel(client, alice, password=KEY)
    url = f"/channels/{ch['id']}/join"

    # No password: rejected.
    assert (await client.post(url, headers=bob["headers"])).status_code == 403
    # Wrong password: rejected.
    r = await client.post(url, json={"password": "nope-nope-nope"}, headers=bob["headers"])
    assert r.status_code == 403
    # Correct password: joins.
    r = await client.post(url, json={"password": KEY}, headers=bob["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["is_member"] is True


async def test_admin_bypasses_password(client, alice, bob):
    ch = await _new_channel(client, alice, password=KEY)
    await _make_admin(bob["id"])
    r = await client.post(f"/channels/{ch['id']}/join", headers=bob["headers"])
    assert r.status_code == 200, r.text


async def test_owner_can_set_and_remove_password(client, alice, bob):
    ch = await _new_channel(client, alice)  # open channel, no key
    assert ch["has_password"] is False
    url = f"/channels/{ch['id']}"

    # Set a key.
    r = await client.patch(url, json={"password": KEY}, headers=alice["headers"])
    assert r.status_code == 200 and r.json()["has_password"] is True
    assert (
        await client.post(f"{url}/join", headers=bob["headers"])
    ).status_code == 403

    # Remove it (empty string): channel is open again.
    r = await client.patch(url, json={"password": ""}, headers=alice["headers"])
    assert r.status_code == 200 and r.json()["has_password"] is False
    assert (
        await client.post(f"{url}/join", headers=bob["headers"])
    ).status_code == 200


async def test_password_rejected_on_private_channel(client, alice):
    ch = await _new_channel(client, alice, is_private=True, password=KEY)
    # A private channel ignores the key at creation.
    assert ch["has_password"] is False
    # And setting one via update is refused.
    r = await client.patch(
        f"/channels/{ch['id']}", json={"password": KEY}, headers=alice["headers"]
    )
    assert r.status_code == 400
