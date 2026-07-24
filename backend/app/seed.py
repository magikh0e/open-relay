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
    (
        "1.16.0",
        "📲 v1.16.0 — Install it like an app\n"
        "• Open Relay can now be added to your home screen or desktop and "
        "opens in its own window, without browser chrome\n"
        "• On Android and desktop Chrome look for 'Install'; on iPhone use "
        "Share → Add to Home Screen\n"
        "• The app shell loads offline, though you'll still need a connection "
        "to send or receive anything",
    ),
    (
        "1.17.0",
        "📜 v1.17.0 — Terms of service\n"
        "• A plain-English terms page now sits alongside the privacy policy, "
        "linked from the sign-in screen\n"
        "• It covers what's expected of you, how moderation works, and is "
        "blunt that the service comes with no guarantees\n"
        "• Signing in with Discord now works",
    ),
    (
        "1.18.0",
        "🔔 v1.18.0 — Notifications, formatting, and your data\n"
        "• Turn on notifications in your profile to hear about DMs and "
        "mentions even with the app closed — they say who and where, never "
        "what was said\n"
        "• Write **bold**, *italic*, `code` and ```fenced blocks``` — pasted "
        "code finally stays readable\n"
        "• Encrypted conversations now show a safety number: read it aloud to "
        "the other person and if it matches, nobody is in the middle\n"
        "• Search results jump straight to the message and highlight it\n"
        "• Half-written messages are kept per channel when you switch away\n"
        "• Download everything we hold about you, or delete your account "
        "outright, from your profile",
    ),
    (
        "1.18.1",
        "⌨️ v1.18.1 — Code blocks actually work now\n"
        "• Fixed ```code blocks``` coming out empty\n"
        "• The message box now takes multiple lines: Shift+Enter for a new "
        "line, Enter to send\n"
        "• Existing blank-looking messages fix themselves — the text was "
        "always there, it just wasn't being shown",
    ),
    (
        "1.19.0",
        "🎨 v1.19.0 — A visual polish pass\n"
        "• Avatars are now colour-coded per person, so conversations are far "
        "easier to scan\n"
        "• Smoother hovers, presses and modal transitions throughout\n"
        "• Slimmer, theme-matched scrollbars\n"
        "• Big channels no longer list every offline member at once — a "
        "'Show more' keeps things quick\n"
        "• Mentions read as pills, and reactions show more clearly when you've "
        "reacted",
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
