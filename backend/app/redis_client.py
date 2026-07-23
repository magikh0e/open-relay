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
# We track online users as a hash of user_id -> connection count so a user
# with multiple open tabs/devices stays "online" until the last one closes.
PRESENCE_KEY = "presence:online"
USER_TOPIC = "user:{user_id}"  # personal channel (e.g. "you were added to a DM")


def user_topic(user_id: str) -> str:
    return USER_TOPIC.format(user_id=user_id)


async def mark_online(user_id: str) -> int:
    """Increment connection count; returns new count."""
    count = await redis_client.hincrby(PRESENCE_KEY, user_id, 1)
    return count


async def mark_offline(user_id: str) -> int:
    """Decrement connection count; returns remaining count (>=0)."""
    count = await redis_client.hincrby(PRESENCE_KEY, user_id, -1)
    if count <= 0:
        await redis_client.hdel(PRESENCE_KEY, user_id)
        return 0
    return count


async def online_user_ids() -> set[str]:
    data = await redis_client.hgetall(PRESENCE_KEY)
    return set(data.keys())
