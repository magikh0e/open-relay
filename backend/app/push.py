"""Web Push notifications.

Deliberate constraint: a push payload passes through Google's or Mozilla's push
service, so it must never carry message content. Doing so would hand the
plaintext of an end-to-end encrypted DM to a third party — exactly what the
encryption exists to prevent. Payloads therefore carry only who and where
("Alice sent you a message"), never what.
"""
import asyncio
import base64
import json
import logging

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select

from .config import settings
from .database import SessionLocal
from .models import AppSecret, PushSubscription

log = logging.getLogger(__name__)

VAPID_PRIVATE = "vapid_private_key"
VAPID_PUBLIC = "vapid_public_key"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _generate_keypair() -> tuple[str, str]:
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    return _b64(private), _b64(public)


async def get_vapid_keys() -> tuple[str, str]:
    """Return (private, public), generating and persisting them on first use.

    Kept in the database rather than the environment so a fresh deployment has
    working push without anyone editing env files, and so the pair stays stable
    — regenerating it would silently invalidate every existing subscription.
    Explicit env settings still win if present.
    """
    if settings.vapid_private_key and settings.vapid_public_key:
        return settings.vapid_private_key, settings.vapid_public_key

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(AppSecret).where(
                    AppSecret.key.in_([VAPID_PRIVATE, VAPID_PUBLIC])
                )
            )
        ).scalars().all()
        found = {r.key: r.value for r in rows}
        if VAPID_PRIVATE in found and VAPID_PUBLIC in found:
            return found[VAPID_PRIVATE], found[VAPID_PUBLIC]

        private, public = _generate_keypair()
        db.add(AppSecret(key=VAPID_PRIVATE, value=private))
        db.add(AppSecret(key=VAPID_PUBLIC, value=public))
        try:
            await db.commit()
        except Exception:
            # Another worker generated them first — take theirs.
            await db.rollback()
            rows = (
                await db.execute(
                    select(AppSecret).where(
                        AppSecret.key.in_([VAPID_PRIVATE, VAPID_PUBLIC])
                    )
                )
            ).scalars().all()
            found = {r.key: r.value for r in rows}
            return found[VAPID_PRIVATE], found[VAPID_PUBLIC]
        return private, public


def _send_one(sub: dict, payload: str, private_key: str, subject: str) -> int:
    """Blocking send. Returns the HTTP status (410/404 mean 'forget this one')."""
    try:
        res = webpush(
            subscription_info=sub,
            data=payload,
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            timeout=10,
        )
        return getattr(res, "status_code", 200)
    except WebPushException as exc:
        return getattr(exc.response, "status_code", 0) if exc.response else 0


async def notify(user_id: str, title: str, body: str, url: str, tag: str) -> None:
    """Push to every device a user has registered. Never raises."""
    try:
        private, _ = await get_vapid_keys()
        async with SessionLocal() as db:
            subs = (
                await db.execute(
                    select(PushSubscription).where(
                        PushSubscription.user_id == user_id
                    )
                )
            ).scalars().all()
            if not subs:
                return
            payload = json.dumps(
                {"title": title, "body": body, "url": url, "tag": tag}
            )
            subject = f"mailto:admin@{settings.public_base_url.split('//')[-1]}"

            dead: list[str] = []
            for s in subs:
                info = {
                    "endpoint": s.endpoint,
                    "keys": {"p256dh": s.p256dh, "auth": s.auth},
                }
                status = await asyncio.to_thread(
                    _send_one, info, payload, private, subject
                )
                if status in (404, 410):
                    dead.append(s.id)
            if dead:
                # Endpoint is gone for good; stop trying it.
                await db.execute(
                    delete(PushSubscription).where(PushSubscription.id.in_(dead))
                )
                await db.commit()
    except Exception:  # pragma: no cover - notification must never break a send
        log.exception("push notification failed")


def notify_in_background(*args, **kwargs) -> None:
    """Fire-and-forget so sending a message never waits on a push service."""
    try:
        asyncio.get_running_loop().create_task(notify(*args, **kwargs))
    except RuntimeError:  # pragma: no cover - no loop (tests/CLI)
        pass
