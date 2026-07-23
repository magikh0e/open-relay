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
# Keep only the LAST 3 RELEASES here: versions dropped from this list have their
# posts pruned from the channel on the next boot.
# Oldest first so the newest release lands at the bottom of the channel.
WHATSNEW_POSTS = [
    (
        "1.6.0",
        "📣 v1.6.0 — Announcements + tidier messages\n"
        "• New read-only #whatsnew channel for release notes — you're reading it\n"
        "• React to any update; replies are disabled here\n"
        "• Message actions (reply, thread, react) now sit next to the message "
        "instead of out at the far right",
    ),
    (
        "1.7.0",
        "📄 v1.7.0 — Privacy policy\n"
        "• A plain-English privacy page now explains exactly what is stored "
        "and who can see it — linked from the login screen\n"
        "• It covers the awkward bits too: upload links are public, and "
        "deleted messages are retained",
    ),
    (
        "1.8.0",
        "🔒 v1.8.0 — Encrypted direct messages\n"
        "• DMs can now be end-to-end encrypted — turn it on from any DM\n"
        "• Your key is generated in your browser and protected by a "
        "passphrase; the server only ever stores scrambled text\n"
        "• Both people need it switched on, and a lock icon shows when a "
        "conversation is protected\n"
        "• Forget the passphrase and those messages are unrecoverable — "
        "there is no reset",
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

        # Rolling window: drop system posts for versions that have aged out of
        # WHATSNEW_POSTS, so the channel only ever shows the latest few.
        keep = [f"v{v} —" for v, _ in WHATSNEW_POSTS]
        for m in system_msgs:
            if not any(marker in (m.content or "") for marker in keep):
                await db.delete(m)

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
