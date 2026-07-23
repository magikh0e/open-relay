"""Idempotent seeding of system channels, run on startup.

Currently ensures the read-only #whatsnew announcement channel exists and that
every user is a member of it (so release-note posts reach everyone without a
manual join). Safe to run on every boot.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .database import SessionLocal
from .models import (
    KIND_PUBLIC,
    ROLE_MEMBER,
    Channel,
    ChannelMember,
    Message,
    User,
)

WHATSNEW_SLUG = "whatsnew"
WHATSNEW_TOPIC = "Release notes & product updates — react, don't reply."

# Canonical release notes, posted with sender_id=None (system authored) — the
# UI labels these "Relay". Keyed by version and upserted on every boot, so
# adding an entry publishes it and editing one corrects the live post in place.
# Oldest first so the newest release lands at the bottom of the channel.
WHATSNEW_POSTS = [
    (
        "1.4.0",
        "📱 v1.4.0 — Swipe navigation\n"
        "• Swipe right on a chat to go back to your channel list\n"
        "• Swipe left to open the member roster\n"
        "• Swipe the roster away to close it",
    ),
    (
        "1.5.0",
        "🖼️ v1.5.0 — Faster image uploads\n"
        "• Photos are resized and re-encoded in your browser before uploading\n"
        "• Multi-MB phone photos now upload in a fraction of the time\n"
        "• That re-encoding happens on your device and removes location "
        "(EXIF/GPS) data, so it never reaches the server\n"
        "• GIFs and documents are left untouched",
    ),
    (
        "1.6.0",
        "📣 v1.6.0 — Announcements + tidier messages\n"
        "• New read-only #whatsnew channel for release notes — you're reading it\n"
        "• React to any update; replies are disabled here\n"
        "• Message actions (reply, thread, react) now sit next to the message "
        "instead of out at the far right",
    ),
]


async def ensure_whatsnew() -> None:
    async with SessionLocal() as db:
        ch = (
            await db.execute(select(Channel).where(Channel.slug == WHATSNEW_SLUG))
        ).scalar_one_or_none()

        if ch is None:
            ch = Channel(
                kind=KIND_PUBLIC,
                slug=WHATSNEW_SLUG,
                name="whatsnew",
                topic=WHATSNEW_TOPIC,
                read_only=True,
                created_by=None,  # system-owned
            )
            db.add(ch)
            await db.flush()
        elif not ch.read_only:
            ch.read_only = True

        # Backfill membership for every user so the channel is always present.
        user_ids = (await db.execute(select(User.id))).scalars().all()
        member_ids = set(
            (
                await db.execute(
                    select(ChannelMember.user_id).where(
                        ChannelMember.channel_id == ch.id
                    )
                )
            ).scalars().all()
        )
        for uid in user_ids:
            if uid not in member_ids:
                db.add(
                    ChannelMember(
                        channel_id=ch.id, user_id=uid, role=ROLE_MEMBER
                    )
                )

        # Upsert the release notes, matched on the "vX.Y.Z —" marker: insert the
        # ones that aren't posted yet, and rewrite any whose wording changed.
        # Keyed this way, reboots never duplicate and corrections propagate.
        system_msgs = (
            await db.execute(
                select(Message).where(
                    Message.channel_id == ch.id,
                    Message.sender_id.is_(None),
                )
            )
        ).scalars().all()

        now = datetime.now(timezone.utc)
        for i, (version, content) in enumerate(WHATSNEW_POSTS):
            marker = f"v{version} —"
            found = next(
                (m for m in system_msgs if marker in (m.content or "")), None
            )
            if found is None:
                db.add(
                    Message(
                        channel_id=ch.id,
                        sender_id=None,  # system authored
                        content=content,
                        # Stagger so ordering by created_at is deterministic.
                        created_at=now
                        - timedelta(seconds=len(WHATSNEW_POSTS) - i),
                    )
                )
            elif found.content != content:
                found.content = content

        await db.commit()
