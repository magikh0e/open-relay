"""Retention sweep: deleted messages and orphaned files must actually go."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.models import Message, Upload
from app.purge import purge_once

pytestmark = pytest.mark.asyncio


async def _dm(client, a, b):
    res = await client.post("/dms", headers=a["headers"], json={"user_id": b["id"]})
    return res.json()["id"]


async def test_recent_deletions_are_kept(client, alice, bob):
    """The sweep must not eat something deleted a moment ago."""
    cid = await _dm(client, alice, bob)
    msg = (
        await client.post(
            f"/channels/{cid}/messages",
            headers=alice["headers"],
            json={"content": "delete me"},
        )
    ).json()
    await client.delete(f"/channels/{cid}/messages/{msg['id']}", headers=alice["headers"])

    await purge_once()

    async with SessionLocal() as db:
        still_there = await db.get(Message, msg["id"])
    assert still_there is not None


async def test_old_deleted_messages_are_hard_deleted(client, alice, bob):
    cid = await _dm(client, alice, bob)
    msg = (
        await client.post(
            f"/channels/{cid}/messages",
            headers=alice["headers"],
            json={"content": "ancient"},
        )
    ).json()
    await client.delete(f"/channels/{cid}/messages/{msg['id']}", headers=alice["headers"])

    # Backdate the deletion past the retention window.
    async with SessionLocal() as db:
        row = await db.get(Message, msg["id"])
        row.deleted_at = datetime.now(timezone.utc) - timedelta(
            days=settings.purge_after_days + 1
        )
        await db.commit()

    await purge_once()

    async with SessionLocal() as db:
        assert await db.get(Message, msg["id"]) is None


async def test_orphaned_upload_file_is_removed_from_disk(client, alice):
    """An upload nothing references any more shouldn't sit on disk forever."""
    res = await client.post(
        "/uploads",
        headers=alice["headers"],
        files={"file": ("orphan.txt", b"bytes on disk", "text/plain")},
    )
    att = res.json()

    async with SessionLocal() as db:
        up = await db.get(Upload, att["id"])
        stored = up.stored_name
        up.created_at = datetime.now(timezone.utc) - timedelta(
            days=settings.purge_after_days + 1
        )
        await db.commit()

    path = Path(settings.upload_dir) / stored
    assert path.exists(), "file should exist before the sweep"

    await purge_once()

    assert not path.exists(), "orphaned file left on disk"
    async with SessionLocal() as db:
        assert await db.get(Upload, att["id"]) is None


async def test_attached_upload_survives(client, alice, bob):
    """A file still referenced by a live message must not be swept."""
    cid = await _dm(client, alice, bob)
    att = (
        await client.post(
            "/uploads",
            headers=alice["headers"],
            files={"file": ("keep.txt", b"keep me", "text/plain")},
        )
    ).json()
    await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "", "upload_id": att["id"]},
    )

    async with SessionLocal() as db:
        up = await db.get(Upload, att["id"])
        stored = up.stored_name
        up.created_at = datetime.now(timezone.utc) - timedelta(
            days=settings.purge_after_days + 1
        )
        await db.commit()

    await purge_once()

    assert (Path(settings.upload_dir) / stored).exists()
    async with SessionLocal() as db:
        assert await db.get(Upload, att["id"]) is not None
