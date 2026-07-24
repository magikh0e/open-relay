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
# UI labels these "Open Relay". Keyed by version and upserted on every boot, so
# adding an entry publishes it and editing one corrects the live post in place.
# Keep only the LAST 10 RELEASES here: versions dropped from this list have
# their posts pruned from the channel on the next boot.
# Oldest first so the newest release lands at the bottom of the channel.
WHATSNEW_POSTS = [
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
        "• Forget the passphrase and those messages are unrecoverable — there "
        "is no reset, and the admin can't help either: the key they store is "
        "locked with your passphrase",
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
        "• Appear offline while still using Open Relay normally\n"
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
    (
        "1.13.0",
        "🧰 v1.13.0 — Unread badges, older messages, encrypted files\n"
        "• Unread counts in the sidebar, with a highlighted badge when someone "
        "mentions you\n"
        "• Scroll up to load earlier messages — history is no longer capped at "
        "the most recent 50\n"
        "• Files sent in an encrypted DM are now encrypted too; the server "
        "can't see the contents, the name or even the file type\n"
        "• Changing your password now signs out your other devices\n"
        "• Deleted messages and leftover files are cleared for real after 30 days",
    ),
    (
        "1.14.0",
        "👋 v1.14.0 — An intro page, and a friendlier interface\n"
        "• New About page explaining what Open Relay is, what it protects and what "
        "it doesn't — linked from the sign-in screen\n"
        "• Confirmations now appear in-app instead of as browser popups\n"
        "• Dialogs close with Escape and keep keyboard focus inside them\n"
        "• Icon buttons are properly labelled for screen readers\n"
        "• On phones you now land on your channel list instead of being "
        "dropped into a conversation",
    ),
    (
        "1.15.0",
        "✨ v1.15.0 — We're now Open Relay\n"
        "• New name and logo across the app, sign-in screen and About page\n"
        "• The tab now shows a proper icon instead of a blank page symbol\n"
        "• Nothing about your account, messages or keys changes — it's the "
        "same service, just properly dressed",
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
        total = len(WHATSNEW_POSTS)

        def slot(i: int) -> datetime:
            """Timestamp for position i — one second apart, oldest first."""
            return now - timedelta(seconds=total - i)

        for i, (version, content) in enumerate(WHATSNEW_POSTS):
            found = canonical.get(version)
            if found is None:
                created = Message(
                    channel_id=ch.id,
                    sender_id=None,  # system authored
                    content=content,
                    created_at=slot(i),
                )
                db.add(created)
                canonical[version] = created
            elif found.content != content:
                found.content = content

        # The channel is ordered by created_at, but each note otherwise keeps
        # the timestamp of whichever deploy first posted it — so a version
        # backfilled later (or re-added after ageing out) sorts into the wrong
        # place. Renormalise, but only when the order is actually wrong, so the
        # timestamps don't churn on every boot.
        seq = [canonical[v] for v, _ in WHATSNEW_POSTS if v in canonical]
        out_of_order = any(
            seq[k].created_at >= seq[k + 1].created_at
            for k in range(len(seq) - 1)
        )
        if out_of_order:
            for i, (version, _) in enumerate(WHATSNEW_POSTS):
                msg = canonical.get(version)
                if msg is not None:
                    msg.created_at = slot(i)

        await db.commit()
