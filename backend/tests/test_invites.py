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


async def test_invite_provenance_on_profile_and_list(client, alice, monkeypatch):
    await _make_admin(alice["id"])
    code = (await client.post("/invites", headers=alice["headers"])).json()["code"]

    monkeypatch.setattr(settings, "registration_mode", "invite")
    monkeypatch.setattr(settings, "register_rate_per_hour", 1000)
    u = uuid4().hex[:8]
    reg = await client.post(
        "/auth/register",
        json={
            "username": "prov" + u,
            "email": f"prov{u}@example.com",
            "password": "password123",
            "invite_code": code,
        },
    )
    assert reg.status_code == 201, reg.text
    new_headers = {"Authorization": f"Bearer {reg.json()['access_token']}"}
    new_id = (await client.get("/users/me", headers=new_headers)).json()["id"]

    # The invited user's profile names who invited them.
    prof = (await client.get(f"/users/{new_id}", headers=alice["headers"])).json()
    assert prof["registered_via_invite"] is True
    assert prof["invited_by_username"] == alice["username"]

    # Alice (created on an open server in the fixture) shows no invite.
    mine = (await client.get(f"/users/{alice['id']}", headers=alice["headers"])).json()
    assert mine["registered_via_invite"] is False
    assert mine["invited_by_username"] is None

    # The admin list reflects the redemption too.
    codes = (await client.get("/invites", headers=alice["headers"])).json()
    used = next(c for c in codes if c["code"] == code)
    assert used["used_by_username"] == "prov" + u
    assert used["created_by_username"] == alice["username"]
