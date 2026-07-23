import time

import redis.asyncio as redis

from .config import settings

# Shared connection pool. redis-py asyncio is safe to share across tasks.
redis_client: redis.Redis = redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


def room_topic(channel_id: str) -> str:
    """Redis pub/sub topic for a channel's live events."""
    return f"room:{channel_id}"


async def rate_limit_hit(key: str, limit: int, window_seconds: int) -> bool:
    """Fixed-window counter. Returns True if this hit exceeds `limit` within
    `window_seconds`. Used for login throttling, message flood control, etc."""
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, window_seconds)
    return count > limit


# Presence keys ------------------------------------------------------------
# Presence is a sorted set of individual connections, scored by the time they
# expire: member "<user_id>:<connection_id>", score = unix expiry.
#
# It used to be a hash of user_id -> connection count, incremented on connect
# and decremented in a `finally` on disconnect. When a worker died with sockets
# open — which happens on every deploy — that decrement never ran and the count
# leaked, leaving users permanently "online". Expiry-based membership can't
# drift: a dead worker's entries simply age out, and live clients re-assert
# themselves on their next heartbeat.
PRESENCE_KEY = "presence:online"
# Clients ping every 25s; allow a couple of misses before dropping someone.
PRESENCE_TTL = 75
USER_TOPIC = "user:{user_id}"  # personal channel (e.g. "you were added to a DM")


def user_topic(user_id: str) -> str:
    return USER_TOPIC.format(user_id=user_id)


def _member(user_id: str, conn_id: str) -> str:
    return f"{user_id}:{conn_id}"


async def _live_members() -> list[str]:
    """Live connection members, purging any that have expired."""
    now = time.time()
    await redis_client.zremrangebyscore(PRESENCE_KEY, "-inf", now)
    return await redis_client.zrange(PRESENCE_KEY, 0, -1)


async def _count_for(user_id: str) -> int:
    prefix = f"{user_id}:"
    return sum(1 for m in await _live_members() if m.startswith(prefix))


async def mark_online(user_id: str, conn_id: str) -> int:
    """Register a connection; returns the user's live connection count."""
    await redis_client.zadd(
        PRESENCE_KEY, {_member(user_id, conn_id): time.time() + PRESENCE_TTL}
    )
    return await _count_for(user_id)


async def touch_presence(user_id: str, conn_id: str) -> None:
    """Extend a connection's lease. Called on each client heartbeat, which is
    also what lets live sessions restore themselves after a worker restart."""
    await redis_client.zadd(
        PRESENCE_KEY, {_member(user_id, conn_id): time.time() + PRESENCE_TTL}
    )


async def mark_offline(user_id: str, conn_id: str) -> int:
    """Drop a connection; returns the user's remaining live connection count."""
    await redis_client.zrem(PRESENCE_KEY, _member(user_id, conn_id))
    return await _count_for(user_id)


async def reset_presence() -> None:
    """Clear presence at startup.

    Also migrates the old hash-shaped key, which would otherwise make every
    ZADD fail with WRONGTYPE. Safe to run even while other workers hold live
    sockets: their clients re-register within one heartbeat (~25s).
    """
    await redis_client.delete(PRESENCE_KEY)


async def online_user_ids() -> set[str]:
    return {m.split(":", 1)[0] for m in await _live_members()}


# Away status: user_id -> away message ("" is treated as not-away).
AWAY_KEY = "presence:away"


async def set_away(user_id: str, message: str) -> None:
    await redis_client.hset(AWAY_KEY, user_id, message or "away")


async def clear_away(user_id: str) -> None:
    await redis_client.hdel(AWAY_KEY, user_id)


async def away_map() -> dict[str, str]:
    return await redis_client.hgetall(AWAY_KEY)
