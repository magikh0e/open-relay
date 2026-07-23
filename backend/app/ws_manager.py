"""Real-time layer.

Each worker process holds live WebSocket connections. A single Redis pub/sub
subscription per worker bridges events between workers: when any worker
publishes to `room:{channel_id}`, every worker that has a socket subscribed to
that room forwards the payload to its local clients.

Flow for a chat message:
  1. HTTP/WS handler persists the Message row in Postgres.
  2. Handler publishes JSON to Redis topic `room:{channel_id}`.
  3. Every worker's bridge receives it and pushes to matching local sockets.

This means horizontal scaling (multiple gunicorn/uvicorn workers or hosts)
works with no sticky sessions required.
"""
import asyncio
import json
from collections import defaultdict

from fastapi import WebSocket

from .redis_client import redis_client, room_topic, user_topic


class ConnectionManager:
    def __init__(self) -> None:
        # channel/user topic -> set of local WebSocket connections
        self._rooms: dict[str, set[WebSocket]] = defaultdict(set)
        # reverse index so we can clean up on disconnect
        self._socket_topics: dict[WebSocket, set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._pubsub = None
        self._reader_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin listening to Redis. Called once on app startup."""
        self._pubsub = redis_client.pubsub()
        # Subscribe to the wildcard so we don't have to (un)subscribe per room;
        # filtering happens locally against self._rooms. Fine up to moderate
        # scale; shard by pattern if this ever gets hot.
        await self._pubsub.psubscribe("room:*", "user:*")
        self._reader_task = asyncio.create_task(self._reader())

    async def stop(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        if self._pubsub:
            await self._pubsub.aclose()

    async def _reader(self) -> None:
        assert self._pubsub is not None
        async for msg in self._pubsub.listen():
            if msg.get("type") != "pmessage":
                continue
            topic = msg["channel"]
            try:
                payload = json.loads(msg["data"])
            except (ValueError, TypeError):
                continue
            await self._local_broadcast(topic, payload)

    async def _local_broadcast(self, topic: str, payload: dict) -> None:
        sockets = list(self._rooms.get(topic, ()))
        if not sockets:
            return
        text = json.dumps(payload)
        dead: list[WebSocket] = []
        for ws in sockets:
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            await self.unregister(ws)

    # --- local registration ------------------------------------------------

    async def subscribe(self, ws: WebSocket, topic: str) -> None:
        async with self._lock:
            self._rooms[topic].add(ws)
            self._socket_topics[ws].add(topic)

    async def unsubscribe(self, ws: WebSocket, topic: str) -> None:
        async with self._lock:
            self._rooms.get(topic, set()).discard(ws)
            self._socket_topics.get(ws, set()).discard(topic)

    async def unregister(self, ws: WebSocket) -> None:
        async with self._lock:
            topics = self._socket_topics.pop(ws, set())
            for t in topics:
                self._rooms.get(t, set()).discard(ws)

    # --- publishing (cross-worker) ----------------------------------------

    async def publish_room(self, channel_id: str, payload: dict) -> None:
        await redis_client.publish(room_topic(channel_id), json.dumps(payload))

    async def publish_user(self, user_id: str, payload: dict) -> None:
        await redis_client.publish(user_topic(user_id), json.dumps(payload))


manager = ConnectionManager()
