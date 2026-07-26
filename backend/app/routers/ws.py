"""WebSocket endpoint: one socket per client session.

Handshake: client connects to /ws?token=<access_token>. We authenticate,
subscribe the socket to every channel the user belongs to (plus a personal
user topic), and track presence. Chat messages are posted over HTTP (see
routers/messages.py) and arrive back here via the Redis bridge; this socket
also carries lightweight ephemeral signals like typing indicators.
"""
import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select, update

from ..database import SessionLocal
from ..deps import bot_scopes, resolve_bot_token, resolve_token_user
from ..models import ChannelMember, User
from ..redis_client import (
    mark_offline,
    mark_online,
    room_topic,
    touch_presence,
    user_topic,
)
from ..ws_manager import manager

router = APIRouter()


async def _member_channel_ids(user_id: str) -> list[str]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(ChannelMember.channel_id).where(
                    ChannelMember.user_id == user_id
                )
            )
        ).scalars().all()
        return list(rows)


async def _touch_last_active(user_id: str) -> None:
    """Stamp the user as just-seen. Recorded regardless of their privacy
    settings (share_last_active only gates who can read it back)."""
    async with SessionLocal() as db:
        await db.execute(
            update(User)
            .where(User.id == user_id)
            .values(last_active_at=datetime.now(timezone.utc))
        )
        await db.commit()


async def _broadcast_presence(channel_ids: list[str], user_id: str, online: bool) -> None:
    payload = {
        "type": "presence",
        "data": {"user_id": user_id, "online": online},
    }
    for cid in channel_ids:
        await manager.publish_room(cid, payload)


@router.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = "") -> None:
    async with SessionLocal() as db:
        user = await resolve_token_user(db, token)
        if user is None:
            # Bots hold an opaque token rather than a JWT. Listening is the
            # whole point of a bot over a webhook, so the socket accepts one,
            # gated on the same read scope that gates message history.
            user = await resolve_bot_token(db, token)
            if user is not None and "read" not in bot_scopes(user):
                user = None
    if user is None:
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    channel_ids = await _member_channel_ids(user.id)
    await _touch_last_active(user.id)
    # Identifies this specific socket so presence is per-connection rather than
    # a shared counter that can drift.
    conn_id = uuid.uuid4().hex

    # Subscribe to each channel room + this user's personal topic.
    for cid in channel_ids:
        await manager.subscribe(ws, room_topic(cid))
    await manager.subscribe(ws, user_topic(user.id))

    # Presence: only announce "online" on the first connection for this user.
    # Users who've turned presence off are never added to the online set at all,
    # so they read as offline everywhere rather than being filtered per-viewer.
    if user.share_presence:
        count = await mark_online(user.id, conn_id)
        if count == 1:
            await _broadcast_presence(channel_ids, user.id, online=True)

    await ws.send_text(json.dumps({"type": "ready", "data": {"user_id": user.id}}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            await _handle_client_message(ws, user, conn_id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(ws)
        await _touch_last_active(user.id)
        # Mirror the connect path: if we never marked them online, don't
        # decrement the counter (it would drift negative).
        if user.share_presence:
            remaining = await mark_offline(user.id, conn_id)
            if remaining == 0:
                await _broadcast_presence(channel_ids, user.id, online=False)


async def _handle_client_message(
    ws: WebSocket, user, conn_id: str, msg: dict
) -> None:
    user_id = user.id
    mtype = msg.get("type")

    if mtype == "ping":
        # The heartbeat doubles as the presence lease renewal.
        if user.share_presence:
            await touch_presence(user_id, conn_id)
        await ws.send_text(json.dumps({"type": "pong"}))

    elif mtype == "typing":
        # Dropped for users who've turned typing indicators off, so a client
        # that keeps sending them can't leak the signal anyway.
        channel_id = msg.get("channel_id")
        if channel_id and user.share_typing:
            await manager.publish_room(
                channel_id,
                {
                    "type": "typing",
                    "data": {"channel_id": channel_id, "user_id": user_id},
                },
            )

    elif mtype == "subscribe":
        # Called after the client joins/opens a channel so the live socket
        # starts receiving its events without reconnecting.
        channel_id = msg.get("channel_id")
        if channel_id:
            await manager.subscribe(ws, room_topic(channel_id))

    elif mtype == "unsubscribe":
        channel_id = msg.get("channel_id")
        if channel_id:
            await manager.unsubscribe(ws, room_topic(channel_id))
