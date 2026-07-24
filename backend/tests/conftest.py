"""Test fixtures.

Runs against a real Postgres + Redis (the same containers dev uses) rather than
mocks, because most of what's worth testing here — advisory locks, cascade
deletes, rate limiting — only behaves realistically against the real thing.
Each test gets its own uniquely-named users so tests don't collide.
"""
import os
import uuid

import pytest
import pytest_asyncio

os.environ.setdefault("AUTO_CREATE_TABLES", "0")

from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.main import app  # noqa: E402
from app.redis_client import redis_client  # noqa: E402


@pytest_asyncio.fixture
async def client():
    """ASGI client that exercises the real app, without a network listener."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def unique(prefix: str = "u") -> str:
    return f"{prefix}{uuid.uuid4().hex[:10]}"


@pytest_asyncio.fixture
async def user_factory(client):
    """Register throwaway users. Clears the per-IP register throttle first so
    the limiter doesn't fail unrelated tests."""

    async def make(**overrides):
        await redis_client.delete("rl:register:ip:testclient")
        for key in await redis_client.keys("register:ip:*"):
            await redis_client.delete(key)
        name = overrides.pop("username", None) or unique()
        payload = {
            "username": name,
            "email": f"{name}@example.com",
            "password": "password123",
            "display_name": overrides.pop("display_name", None) or name,
            **overrides,
        }
        res = await client.post("/auth/register", json=payload)
        assert res.status_code == 201, res.text
        tokens = res.json()
        me = await client.get(
            "/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        return {
            "username": name,
            "password": "password123",
            "id": me.json()["id"],
            "access": tokens["access_token"],
            "refresh": tokens["refresh_token"],
            "headers": {"Authorization": f"Bearer {tokens['access_token']}"},
        }

    return make


@pytest_asyncio.fixture
async def alice(user_factory):
    return await user_factory()


@pytest_asyncio.fixture
async def bob(user_factory):
    return await user_factory()
