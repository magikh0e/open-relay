"""Idempotent seeding of system channels, run on startup.

Currently ensures the read-only #whatsnew announcement channel exists and that
every user is a member of it (so release-note posts reach everyone without a
manual join). Safe to run on every boot.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

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

# Seeded once, when the channel is first created and still empty. Posted with
# sender_id=None (system authored) — the UI labels these "Relay".
# Oldest first so the newest release lands at the bottom of the channel.
WHATSNEW_POSTS = [
    (
        "📱 v1.4.0 — Swipe navigation\n"
        "• Swipe right on a chat to go back to your channel list\n"
        "• Swipe left to open the member roster\n"
        "• Swipe the roster away to close it"
    ),
    (
        "🖼️ v1.5.0 — Faster image uploads\n"
        "• Photos are resized and compressed in your browser before uploading\n"
        "• Multi-MB phone photos now upload in a fraction of the time\n"
        "• Location (EXIF/GPS) data is stripped from images you share\n"
        "• GIFs and documents are left untouched"
    ),
    (
        "📣 v1.6.0 — Announcements + tidier messages\n"
        "• New read-only #whatsnew channel for release notes — you're reading it\n"
        "• React to any update; replies are disabled here\n"
        "• Message actions (reply, thread, react) now sit next to the message "
        "instead of out at the far right"
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

        # Post the initial release notes, but only while the channel is empty so
        # reboots never duplicate them.
        existing_msgs = (
            await db.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.channel_id == ch.id)
            )
        ).scalar_one()
        if not existing_msgs:
            now = datetime.now(timezone.utc)
            for i, content in enumerate(WHATSNEW_POSTS):
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

        await db.commit()
