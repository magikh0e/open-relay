"""Invite codes: admin gating, and invite-only registration."""
from uuid import uuid4

import pytest
from sqlalchemy import update

from app.config import settings
from app.database import SessionLocal
from app.models import User

pytestmark = pytest.mark.asyncio


async def _make_admin(user_id):
    async with SessionLocal() as db:
        await db.execute(
            update(User).where(User.id == user_id).values(is_admin=True)
        )
        await db.commit()


async def test_only_admins_manage_invites(client, alice, bob):
    assert (await client.post("/invites", headers=alice["headers"])).status_code == 403
    await _make_admin(bob["id"])
    r = await client.post("/invites", headers=bob["headers"])
    assert r.status_code == 201
    assert r.json()["code"]


async def test_registration_config_reflects_mode(client, monkeypatch):
    monkeypatch.setattr(settings, "registration_mode", "open")
    assert (await client.get("/auth/registration")).json()["invite_required"] is False
    monkeypatch.setattr(settings, "registration_mode", "invite")
    assert (await client.get("/auth/registration")).json()["invite_required"] is True


async def test_invite_only_registration(client, alice, monkeypatch):
    await _make_admin(alice["id"])
    code = (await client.post("/invites", headers=alice["headers"])).json()["code"]

    monkeypatch.setattr(settings, "registration_mode", "invite")
    monkeypatch.setattr(settings, "register_rate_per_hour", 1000)  # avoid throttle
    u = uuid4().hex[:8]

    async def reg(uname, extra):
        return await client.post(
            "/auth/register",
            json={
                "username": uname,
                "email": f"{uname}@example.com",
                "password": "password123",
                **extra,
            },
        )

    # No code in invite mode: rejected.
    assert (await reg("iva" + u, {})).status_code == 403
    # Valid code: works.
    ok = await reg("ivb" + u, {"invite_code": code})
    assert ok.status_code == 201, ok.text
    # The same code cannot be reused.
    assert (await reg("ivc" + u, {"invite_code": code})).status_code == 403
