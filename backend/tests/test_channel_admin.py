"""Owner-only gates and the archived-channel guard.

These lock in the v1.24 refactor: a single shared owner check across
edit/delete/role, and a centralized `_active_channel` fetch that makes every
mutation path treat an archived channel as gone.
"""
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.database import SessionLocal
from app.models import Channel, User

pytestmark = pytest.mark.asyncio


async def _make_admin(user_id):
    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(is_admin=True)
        )
        await db.commit()


async def _archive(channel_id):
    async with SessionLocal() as db:
        await db.execute(
            update(Channel).where(Channel.id == channel_id).values(archived=True)
        )
        await db.commit()


async def _new_channel(client, owner, **body):
    slug = "adm" + uuid4().hex[:8]
    payload = {"slug": slug, "name": slug, **body}
    r = await client.post("/channels", json=payload, headers=owner["headers"])
    assert r.status_code == 201, r.text
    return r.json()


# --- owner-only gate ------------------------------------------------------

async def test_non_owner_member_cannot_edit(client, alice, bob):
    ch = await _new_channel(client, alice)
    await client.post(f"/channels/{ch['id']}/join", headers=bob["headers"])
    r = await client.patch(
        f"/channels/{ch['id']}", json={"name": "hijacked"}, headers=bob["headers"]
    )
    assert r.status_code == 403


async def test_site_admin_can_edit_without_membership(client, alice, bob):
    """The unified owner check lets a site admin act on a channel they never
    joined, the same way delete and role already did."""
    ch = await _new_channel(client, alice)
    await _make_admin(bob["id"])
    r = await client.patch(
        f"/channels/{ch['id']}", json={"topic": "by admin"}, headers=bob["headers"]
    )
    assert r.status_code == 200, r.text
    assert r.json()["topic"] == "by admin"


# --- archived guard -------------------------------------------------------

async def test_archived_channel_is_gone_for_reads_and_writes(client, alice):
    ch = await _new_channel(client, alice)
    await _archive(ch["id"])

    # Read paths already 404'd; the write paths now do too.
    got = await client.get(f"/channels/{ch['id']}", headers=alice["headers"])
    assert got.status_code == 404

    patched = await client.patch(
        f"/channels/{ch['id']}", json={"name": "nope"}, headers=alice["headers"]
    )
    assert patched.status_code == 404

    joined = await client.post(f"/channels/{ch['id']}/join", headers=alice["headers"])
    assert joined.status_code == 404


async def test_archived_channel_hidden_from_listing(client, alice):
    ch = await _new_channel(client, alice)
    await _archive(ch["id"])
    listing = await client.get("/channels", headers=alice["headers"])
    assert all(c["id"] != ch["id"] for c in listing.json())
