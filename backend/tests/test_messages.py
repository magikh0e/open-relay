"""Messages: unread tracking, encryption rules, and read-only channels."""
from uuid import uuid4

import pytest

from app.seed import WHATSNEW_SLUG

pytestmark = pytest.mark.asyncio


async def _dm_between(client, a, b):
    res = await client.post("/dms", headers=a["headers"], json={"user_id": b["id"]})
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


async def _channel(client, user, slug):
    res = await client.get("/channels", headers=user["headers"])
    return next(c for c in res.json() if c["slug"] == slug)


async def _new_public_channel(client, owner):
    """A throwaway public channel owned by `owner`.

    Tests that need an ordinary channel create their own rather than reaching
    for a seeded #general, which only exists if someone has run demo_setup.
    Depending on it meant these tests silently skipped in CI.
    """
    slug = "t" + uuid4().hex[:10]
    res = await client.post(
        "/channels",
        headers=owner["headers"],
        json={"slug": slug, "name": slug, "topic": "", "is_private": False},
    )
    assert res.status_code == 201, res.text
    return res.json()


# --- unread badges --------------------------------------------------------

async def test_unread_counts_incoming_messages_not_your_own(client, alice, bob):
    cid = await _dm_between(client, alice, bob)

    await client.post(
        f"/channels/{cid}/messages", headers=alice["headers"], json={"content": "mine"}
    )
    dms = await client.get("/dms", headers=alice["headers"])
    mine = next(d for d in dms.json() if d["id"] == cid)
    assert mine["unread_count"] == 0, "your own message shouldn't be unread"

    for i in range(3):
        await client.post(
            f"/channels/{cid}/messages",
            headers=bob["headers"],
            json={"content": f"hi {i}"},
        )
    dms = await client.get("/dms", headers=alice["headers"])
    mine = next(d for d in dms.json() if d["id"] == cid)
    assert mine["unread_count"] == 3


async def test_mark_read_clears_the_badge(client, alice, bob):
    cid = await _dm_between(client, alice, bob)
    await client.post(
        f"/channels/{cid}/messages", headers=bob["headers"], json={"content": "yo"}
    )

    res = await client.post(f"/channels/{cid}/read", headers=alice["headers"])
    assert res.status_code == 204

    dms = await client.get("/dms", headers=alice["headers"])
    mine = next(d for d in dms.json() if d["id"] == cid)
    assert mine["unread_count"] == 0


async def test_mentions_counted_separately(client, alice, bob):
    """A mention should be distinguishable from ordinary unread traffic."""
    cid = (await _new_public_channel(client, alice))["id"]
    await client.post(f"/channels/{cid}/join", headers=bob["headers"])
    # Mark read after bob's join notice, so only the two messages below count.
    await client.post(f"/channels/{cid}/read", headers=alice["headers"])

    await client.post(
        f"/channels/{cid}/messages",
        headers=bob["headers"],
        json={"content": "just chatter"},
    )
    await client.post(
        f"/channels/{cid}/messages",
        headers=bob["headers"],
        json={"content": f"hey @{alice['username']} look"},
    )

    chans = await client.get("/channels", headers=alice["headers"])
    ch = next(c for c in chans.json() if c["id"] == cid)
    assert ch["unread_count"] == 2
    assert ch["mention_count"] == 1


# --- encryption rules -----------------------------------------------------

async def test_encrypted_messages_only_allowed_in_dms(client, alice, bob):
    cid = await _dm_between(client, alice, bob)
    ok = await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True},
    )
    assert ok.status_code == 201
    assert ok.json()["encrypted"] is True

    cid = (await _new_public_channel(client, alice))["id"]
    refused = await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True},
    )
    assert refused.status_code == 400


async def test_encrypted_content_is_stored_verbatim(client, alice, bob):
    """Sanitising ciphertext would corrupt it beyond decryption."""
    cid = await _dm_between(client, alice, bob)
    payload = "aGVsbG8gd29ybGQ+PCY=" * 3
    res = await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": payload, "encrypted": True},
    )
    assert res.json()["content"] == payload


async def test_encrypted_messages_cannot_be_edited(client, alice, bob):
    cid = await _dm_between(client, alice, bob)
    msg = (
        await client.post(
            f"/channels/{cid}/messages",
            headers=alice["headers"],
            json={"content": "QUJD", "encrypted": True},
        )
    ).json()
    res = await client.patch(
        f"/channels/{cid}/messages/{msg['id']}",
        headers=alice["headers"],
        json={"content": "nope"},
    )
    assert res.status_code == 400


async def test_encrypted_messages_excluded_from_search(client, alice, bob):
    cid = await _dm_between(client, alice, bob)
    await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "SUPERSECRETCIPHERTEXT", "encrypted": True},
    )
    res = await client.get("/search?q=SUPERSECRETCIPHERTEXT", headers=alice["headers"])
    assert res.json() == []


# --- read-only channel ----------------------------------------------------

async def test_whatsnew_is_read_only_for_normal_users(client, alice):
    ch = await _channel(client, alice, WHATSNEW_SLUG)
    assert ch["read_only"] is True
    res = await client.post(
        f"/channels/{ch['id']}/messages",
        headers=alice["headers"],
        json={"content": "can I post?"},
    )
    assert res.status_code == 403


async def test_whatsnew_cannot_be_left(client, alice):
    ch = await _channel(client, alice, WHATSNEW_SLUG)
    res = await client.post(f"/channels/{ch['id']}/leave", headers=alice["headers"])
    assert res.status_code == 403


async def test_new_users_are_auto_joined_to_whatsnew(client, alice):
    ch = await _channel(client, alice, WHATSNEW_SLUG)
    assert ch["is_member"] is True
