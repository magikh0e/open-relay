"""Background retention: actually delete what users asked to have deleted.

Messages are soft-deleted so they vanish for everyone immediately, but the row
(and its content) used to be kept forever, and an upload was never removed when
its message went — so "delete" didn't delete and storage only ever grew. This
sweeps both on a timer.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, select, text

from .config import settings
from .database import SessionLocal
from .models import Message, Upload

log = logging.getLogger(__name__)

# Same advisory-lock trick as seeding: only one worker should sweep.
PURGE_LOCK_KEY = 8_274_100_120


async def purge_once() -> dict[str, int]:
    """One sweep. Returns counts, mostly so tests can assert on them."""
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=settings.purge_after_days
    )
    removed_messages = 0
    removed_files = 0

    async with SessionLocal() as db:
        locked = (
            await db.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": PURGE_LOCK_KEY}
            )
        ).scalar_one()
        if not locked:
            return {"messages": 0, "files": 0}
        try:
            # 1. Hard-delete messages soft-deleted longer ago than the cutoff.
            doomed = (
                await db.execute(
                    select(Message.id).where(
                        Message.deleted_at.is_not(None),
                        Message.deleted_at < cutoff,
                    )
                )
            ).scalars().all()
            if doomed:
                await db.execute(
                    delete(Message).where(Message.id.in_(doomed))
                )
                removed_messages = len(doomed)

            # 2. Delete uploads no live message references any more. Uploads are
            #    detached (SET NULL) when their message goes, so an orphan is
            #    simply one with no message pointing at it.
            referenced = select(Message.upload_id).where(
                Message.upload_id.is_not(None)
            )
            orphans = (
                await db.execute(
                    select(Upload).where(
                        Upload.id.not_in(referenced),
                        Upload.created_at < cutoff,
                    )
                )
            ).scalars().all()
            upload_dir = Path(settings.upload_dir)
            for up in orphans:
                try:
                    (upload_dir / up.stored_name).unlink(missing_ok=True)
                except OSError:  # pragma: no cover - disk-level failure
                    log.warning("could not unlink %s", up.stored_name)
                await db.delete(up)
                removed_files += 1

            await db.commit()
        finally:
            await db.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": PURGE_LOCK_KEY}
            )
            await db.commit()

    if removed_messages or removed_files:
        log.info(
            "purge: removed %s messages, %s files", removed_messages, removed_files
        )
    return {"messages": removed_messages, "files": removed_files}


async def purge_loop() -> None:
    """Sweep on startup, then once a day."""
    while True:
        try:
            await purge_once()
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - never kill the loop
            log.exception("purge sweep failed")
        await asyncio.sleep(24 * 60 * 60)
