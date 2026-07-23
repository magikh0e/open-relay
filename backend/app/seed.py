"""Idempotent seeding of system channels, run on startup.

Currently ensures the read-only #whatsnew announcement channel exists and that
every user is a member of it (so release-note posts reach everyone without a
manual join). Safe to run on every boot.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text

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

# Arbitrary but fixed key for the Postgres advisory lock that serialises seeding
# across gunicorn workers.
SEED_LOCK_KEY = 8_274_100_119

# Canonical release notes, posted with sender_id=None (system authored) — the
# UI labels these "Relay". Keyed by version and upserted on every boot, so
# adding an entry publishes it and editing one corrects the live post in place.
# Keep only the LAST 10 RELEASES here: versions dropped from this list have
# their posts pruned from the channel on the next boot.
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
        "1.6.1",
        "📍 v1.6.1 — Location data stripped from every photo\n"
        "• Photos are always re-encoded in your browser now, even when that "
        "doesn't make the file smaller\n"
        "• Previously a small or already-optimised image was sent untouched, "
        "so its EXIF/GPS tags went with it\n"
        "• GIFs and documents still upload as-is",
    ),
    (
        "1.6.2",
        "🚫 v1.6.2 — Uploads refuse rather than leak\n"
        "• If an image can't be re-encoded in your browser, the upload is now "
        "refused instead of quietly sending the original\n"
        "• Stops a photo's location data reaching the server in the rare cases "
        "where processing fails",
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
    (
        "1.9.0",
        "✖️ v1.9.0 — Closing DMs + smoother encryption\n"
        "• You can now close a direct message: hover it in the sidebar and hit "
        "✕, or type /close\n"
        "• Closing only hides it for you — nothing is deleted, and it comes "
        "back if either of you writes again\n"
        "• /part now closes a DM instead of complaining that you can't leave one\n"
        "• When the other person switches encryption on, your open conversation "
        "picks it up straight away instead of needing a reopen",
    ),
    (
        "1.9.1",
        "🧹 v1.9.1 — Tidier release notes\n"
        "• Fixed this channel showing the same update several times over\n"
        "• Each release now appears exactly once, however many times the "
        "server restarts",
    ),
    (
        "1.10.0",
        "🔑 v1.10.0 — Change your password\n"
        "• Open your profile and hit Change password to set a new one\n"
        "• You'll need your current password to confirm; if you signed in with "
        "Google and never had one, you can set a password here instead\n"
        "• Separate from your message-encryption passphrase — changing your "
        "password doesn't affect encrypted DMs\n"
        "• Signing in now drops you straight into #whatsnew",
    ),
    (
        "1.11.0",
        "🛡️ v1.11.0 — Privacy settings\n"
        "• Open your profile and hit Privacy to control what you share\n"
        "• Turn off typing indicators so nobody sees “is typing…”\n"
        "• Appear offline while still using Relay normally\n"
        "• Stop new people starting DMs with you, or hide yourself from "
        "user search\n"
        "• All four are enforced by the server, not just hidden in your app",
    ),
    (
        "1.12.0",
        "🟢 v1.12.0 — Accurate online status\n"
        "• Fixed people showing as online long after they'd gone\n"
        "• Every server update used to strand whoever was connected at the "
        "time, and they'd stay lit up indefinitely\n"
        "• Online status is now a short lease your app keeps renewing, so it "
        "corrects itself within a minute no matter what",
    ),
]


async def ensure_whatsnew() -> None:
    async with SessionLocal() as db:
        # Every gunicorn worker runs this on startup. Without serialising them
        # they each query, find nothing, and insert their own copy of every
        # release note — which is how production ended up with four copies of
        # one entry. The lock is transaction-scoped and released on commit, so
        # later workers wait, then see the rows the first one wrote.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"), {"key": SEED_LOCK_KEY}
        )

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

        # Reconcile the release notes against WHATSNEW_POSTS, matching on the
        # "vX.Y.Z —" marker. Oldest first so that when duplicates exist we keep
        # the original and drop the later copies.
        system_msgs = (
            await db.execute(
                select(Message)
                .where(
                    Message.channel_id == ch.id,
                    Message.sender_id.is_(None),
                )
                .order_by(Message.created_at)
            )
        ).scalars().all()

        markers = {version: f"v{version} —" for version, _ in WHATSNEW_POSTS}
        canonical: dict[str, Message] = {}
        for m in system_msgs:
            body = m.content or ""
            version = next(
                (v for v, marker in markers.items() if marker in body), None
            )
            if version is None:
                # Aged out of the rolling window.
                await db.delete(m)
            elif version in canonical:
                # A duplicate of one we're already keeping.
                await db.delete(m)
            else:
                canonical[version] = m

        now = datetime.now(timezone.utc)
        for i, (version, content) in enumerate(WHATSNEW_POSTS):
            found = canonical.get(version)
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
