"""WebSocket endpoint: one socket per client session.

Handshake: client connects to /ws?token=<access_token>. We authenticate,
subscribe the socket to every channel the user belongs to (plus a personal
user topic), and track presence. Chat messages are posted over HTTP (see
routers/messages.py) and arrive back here via the Redis bridge; this socket
also carries lightweight ephemeral signals like typing indicators.
"""
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from ..database import SessionLocal
from ..deps import resolve_token_user
from ..models import ChannelMember
from ..redis_client import mark_offline, mark_online, room_topic, user_topic
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
        await ws.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await ws.accept()
    channel_ids = await _member_channel_ids(user.id)

    # Subscribe to each channel room + this user's personal topic.
    for cid in channel_ids:
        await manager.subscribe(ws, room_topic(cid))
    await manager.subscribe(ws, user_topic(user.id))

    # Presence: only announce "online" on the first connection for this user.
    count = await mark_online(user.id)
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
            await _handle_client_message(ws, user.id, msg)
    except WebSocketDisconnect:
        pass
    finally:
        await manager.unregister(ws)
        remaining = await mark_offline(user.id)
        if remaining == 0:
            await _broadcast_presence(channel_ids, user.id, online=False)


async def _handle_client_message(ws: WebSocket, user_id: str, msg: dict) -> None:
    mtype = msg.get("type")

    if mtype == "ping":
        await ws.send_text(json.dumps({"type": "pong"}))

    elif mtype == "typing":
        channel_id = msg.get("channel_id")
        if channel_id:
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
