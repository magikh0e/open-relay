"""Incoming webhooks.

A channel owner/mod (or a site admin) creates a webhook, which mints a secret
URL. Any external system that POSTs `{"text": "...", "name": "..."}` to that URL
posts a message into the channel, shown from the webhook's display name. Useful
for piping CI, alerting, or home-automation events into a channel.

Management routes are JWT-authenticated and gated to channel moderators. The
invoke route is public and authenticated only by the secret token in its path.
"""
import secrets

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from ..config import settings
from ..deps import DB, CurrentUser
from ..models import KIND_DM, Channel, Message, Webhook
from ..redis_client import redis_client
from ..sanitize import sanitize_text
from ..schemas import WebhookCreate, WebhookCreated, WebhookMessageIn, WebhookOut
from ..ws_manager import manager
from .channels import _require_moderator
from .messages import _mention_outs, _msg_out, _replace_mentions, _resolve_mentions

router = APIRouter(tags=["webhooks"])

WEBHOOK_RATE_PER_MIN = 60


def _invoke_url(webhook: Webhook) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/webhooks/{webhook.id}/{webhook.token}"


async def _channel_for_moderation(db, channel_id: str, user) -> Channel:
    channel = await db.get(Channel, channel_id)
    if channel is None or channel.archived or channel.kind == KIND_DM:
        raise HTTPException(status_code=404, detail="Channel not found")
    await _require_moderator(db, channel, user)  # raises 403 if not owner/mod/admin
    return channel


@router.post(
    "/channels/{channel_id}/webhooks",
    response_model=WebhookCreated,
    status_code=201,
)
async def create_webhook(
    channel_id: str, body: WebhookCreate, db: DB, user: CurrentUser
):
    await _channel_for_moderation(db, channel_id, user)
    name = sanitize_text(body.name, max_length=64, allow_newlines=False).strip()
    if not name:
        raise HTTPException(status_code=422, detail="Name required")
    wh = Webhook(
        channel_id=channel_id,
        created_by=user.id,
        token=secrets.token_urlsafe(32),
        name=name,
    )
    db.add(wh)
    await db.flush()  # assigns id + created_at before we read them
    created = WebhookCreated(
        id=wh.id,
        channel_id=wh.channel_id,
        name=wh.name,
        created_at=wh.created_at,
        url=_invoke_url(wh),
    )
    await db.commit()
    return created


@router.get("/channels/{channel_id}/webhooks", response_model=list[WebhookOut])
async def list_webhooks(channel_id: str, db: DB, user: CurrentUser):
    await _channel_for_moderation(db, channel_id, user)
    rows = (
        await db.execute(
            select(Webhook)
            .where(Webhook.channel_id == channel_id)
            .order_by(Webhook.created_at)
        )
    ).scalars().all()
    return [WebhookOut.model_validate(w) for w in rows]


@router.delete("/channels/{channel_id}/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    channel_id: str, webhook_id: str, db: DB, user: CurrentUser
):
    await _channel_for_moderation(db, channel_id, user)
    wh = await db.get(Webhook, webhook_id)
    if wh is None or wh.channel_id != channel_id:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await db.delete(wh)
    await db.commit()


@router.post("/webhooks/{webhook_id}/{token}")
async def invoke_webhook(
    webhook_id: str, token: str, body: WebhookMessageIn, db: DB
):
    wh = await db.get(Webhook, webhook_id)
    # Constant-time compare, and give a uniform 404 so a wrong token can't be
    # distinguished from a missing webhook.
    if wh is None or not secrets.compare_digest(wh.token, token):
        raise HTTPException(status_code=404, detail="Not found")

    key = f"rl:webhook:{wh.id}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 60)
    if count > WEBHOOK_RATE_PER_MIN:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")

    channel = await db.get(Channel, wh.channel_id)
    if channel is None or channel.archived:  # deleted/archived out from under it
        raise HTTPException(status_code=404, detail="Not found")

    content = sanitize_text(body.text, max_length=4000, allow_newlines=True)
    if not content.strip():
        raise HTTPException(status_code=422, detail="Empty message")
    display = sanitize_text(
        body.name or wh.name, max_length=64, allow_newlines=False
    )

    msg = Message(
        channel_id=channel.id,
        sender_id=None,  # webhook posts have no user; author_name carries the name
        content=content,
        author_name=display,
    )
    db.add(msg)
    await db.flush()
    mentioned = await _resolve_mentions(db, content)
    await _replace_mentions(db, msg.id, mentioned)
    await db.commit()

    out = _msg_out(msg, mentions=_mention_outs(mentioned))
    await manager.publish_room(
        channel.id, {"type": "message", "data": out.model_dump(mode="json")}
    )
    return {"ok": True, "id": msg.id}
