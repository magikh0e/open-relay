"""Bot accounts: creation, token auth, and the limits that make them safe.

The point of a bot account is that it is *not* a human session. These pin the
properties that difference rests on: only an admin can mint one, the token is
shown once and stored only as a digest, it authenticates like a bearer token
but cannot reach the endpoints that only make sense for a person, and it holds
no encryption key so it cannot be smuggled into an encrypted group.
"""
import hashlib

import pytest
from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import BotToken, User

pytestmark = pytest.mark.asyncio


async def _make_admin(user_id):
    async with SessionLocal() as db:
        await db.execute(update(User).where(User.id == user_id).values(is_admin=True))
        await db.commit()


async def _new_bot(client, admin, name=None, scopes=("read", "write")):
    import uuid
    body = {"username": name or ("bot" + uuid.uuid4().hex[:8]), "scopes": list(scopes)}
    return await client.post("/bots", json=body, headers=admin["headers"])


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- creation --------------------------------------------------------------

async def test_admin_creates_a_bot_and_sees_the_token_once(client, alice):
    await _make_admin(alice["id"])
    r = await _new_bot(client, alice)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["token"] and len(body["token"]) > 20
    assert set(body["scopes"]) == {"read", "write"}

    # The token is never returned again, only its digest is kept.
    listed = (await client.get("/bots", headers=alice["headers"])).json()
    entry = next(b for b in listed if b["id"] == body["id"])
    assert "token" not in entry

    async with SessionLocal() as db:
        row = (
            await db.execute(select(BotToken).where(BotToken.user_id == body["id"]))
        ).scalar_one()
        assert row.token_hash == hashlib.sha256(body["token"].encode()).hexdigest()
        assert row.token_hash != body["token"]


async def test_non_admin_cannot_create_a_bot(client, alice):
    r = await _new_bot(client, alice)
    assert r.status_code == 403


async def test_unknown_scope_is_refused(client, alice):
    await _make_admin(alice["id"])
    r = await _new_bot(client, alice, scopes=["read", "delete_everything"])
    assert r.status_code == 400
    assert "delete_everything" in r.json()["detail"]


# --- authentication --------------------------------------------------------

async def test_bot_token_authenticates(client, alice):
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    me = await client.get("/users/me", headers=_auth(bot["token"]))
    assert me.status_code == 200
    assert me.json()["id"] == bot["id"]


async def test_a_wrong_token_is_rejected(client, alice):
    await _make_admin(alice["id"])
    await _new_bot(client, alice)
    assert (await client.get("/users/me", headers=_auth("not-a-real-token"))).status_code == 401


async def test_bot_cannot_log_in_with_a_password(client, alice):
    """Bots have no password hash, so the human door is shut to them."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    r = await client.post(
        "/auth/login",
        json={"username_or_email": bot["username"], "password": "password123"},
    )
    assert r.status_code in (401, 403)


async def test_rotating_the_token_invalidates_the_old_one(client, alice):
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    old = bot["token"]
    assert (await client.get("/users/me", headers=_auth(old))).status_code == 200

    rotated = await client.post(f"/bots/{bot['id']}/token", headers=alice["headers"])
    assert rotated.status_code == 200
    new = rotated.json()["token"]
    assert new != old
    # Identity and scopes survive; only the credential changes.
    assert rotated.json()["id"] == bot["id"]
    assert set(rotated.json()["scopes"]) == set(bot["scopes"])

    assert (await client.get("/users/me", headers=_auth(old))).status_code == 401
    assert (await client.get("/users/me", headers=_auth(new))).status_code == 200


async def test_deleting_the_bot_kills_its_token(client, alice):
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    assert (await client.delete(f"/bots/{bot['id']}", headers=alice["headers"])).status_code == 204
    assert (await client.get("/users/me", headers=_auth(bot["token"]))).status_code == 401


# --- what a bot may not do -------------------------------------------------

async def test_bot_is_refused_human_only_endpoints(client, alice):
    """Changing a password, exporting an account, deleting it, or publishing
    encryption keys are all meaningless for a program and dangerous if reachable."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    h = _auth(bot["token"])

    assert (await client.post("/users/me/password", headers=h,
                              json={"new_password": "hunter2hunter2"})).status_code == 403
    assert (await client.get("/users/me/export", headers=h)).status_code == 403
    assert (await client.post("/users/me/delete", headers=h, json={})).status_code == 403
    assert (await client.put("/keys/me", headers=h, json={
        "public_key": "x", "wrapped_private_key": "y", "salt": "z", "iv": "w",
    })).status_code == 403


async def test_bot_cannot_mint_another_bot(client, alice):
    """Bot creation is admin-only, and a bot is never an admin."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    r = await client.post(
        "/bots", json={"username": "sneaky", "scopes": ["write"]},
        headers=_auth(bot["token"]),
    )
    assert r.status_code == 403


async def test_bot_in_a_group_blocks_encryption(client, alice, bob, user_factory):
    """A bot holds no keypair, so it cannot be sealed a group key. The existing
    rule that every member must have a published public key enforces this
    without needing to know what a bot is."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice)).json()
    carol = await user_factory()

    for u in (alice, bob, carol):
        await client.put("/keys/me", headers=u["headers"], json={
            "public_key": "BFAKEPUB==", "wrapped_private_key": "w",
            "salt": "c2FsdA==", "iv": "aXY=",
        })

    gid = (await client.post("/dms/group", headers=alice["headers"], json={
        "user_ids": [bob["id"], carol["id"]], "name": "With a bot",
    })).json()["id"]
    added = await client.post(f"/dms/{gid}/members", headers=alice["headers"],
                              json={"user_id": bot["id"]})
    assert added.status_code == 204

    shares = [
        {"user_id": u, "wrapped_key": "k", "sender_public_key": "BFAKEPUB=="}
        for u in (alice["id"], bob["id"], carol["id"], bot["id"])
    ]
    r = await client.post(f"/dms/{gid}/keys", headers=alice["headers"],
                          json={"shares": shares})
    assert r.status_code == 400
    assert "encryption" in r.json()["detail"].lower()


# --- scope enforcement (stage 2) -------------------------------------------
#
# Access is deny-by-default: a bot may reach only the endpoints in
# deps.BOT_ROUTES, and only with the scope each names. These pin both halves,
# because either one failing open would be silent.

async def _channel_with_bot(client, admin, bot_id, name=None):
    """A channel the bot has been added to, since membership gates it too."""
    import uuid
    slug = "bt" + uuid.uuid4().hex[:8]
    ch = await client.post(
        "/channels", headers=admin["headers"],
        json={"slug": slug, "name": name or slug, "is_private": False},
    )
    assert ch.status_code == 201, ch.text
    cid = ch.json()["id"]
    added = await client.post(
        f"/channels/{cid}/invite", headers=admin["headers"], json={"user_id": bot_id}
    )
    assert added.status_code == 204, added.text
    return cid


async def test_read_scope_gates_history(client, alice):
    await _make_admin(alice["id"])
    reader = (await _new_bot(client, alice, scopes=["read"])).json()
    mute = (await _new_bot(client, alice, scopes=["write"])).json()
    cid = await _channel_with_bot(client, alice, reader["id"])
    await client.post(f"/channels/{cid}/invite", headers=alice["headers"],
                      json={"user_id": mute["id"]})

    assert (await client.get(f"/channels/{cid}/messages",
                             headers=_auth(reader["token"]))).status_code == 200
    # Same channel, same membership: only the scope differs.
    assert (await client.get(f"/channels/{cid}/messages",
                             headers=_auth(mute["token"]))).status_code == 403


async def test_write_scope_gates_posting(client, alice):
    await _make_admin(alice["id"])
    writer = (await _new_bot(client, alice, scopes=["write"])).json()
    reader = (await _new_bot(client, alice, scopes=["read"])).json()
    cid = await _channel_with_bot(client, alice, writer["id"])
    await client.post(f"/channels/{cid}/invite", headers=alice["headers"],
                      json={"user_id": reader["id"]})

    posted = await client.post(f"/channels/{cid}/messages",
                               headers=_auth(writer["token"]),
                               json={"content": "build passed"})
    assert posted.status_code == 201, posted.text
    assert posted.json()["sender"]["is_bot"] is True

    refused = await client.post(f"/channels/{cid}/messages",
                                headers=_auth(reader["token"]),
                                json={"content": "should not land"})
    assert refused.status_code == 403


async def test_react_scope_gates_reactions(client, alice):
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice, scopes=["read", "react"])).json()
    plain = (await _new_bot(client, alice, scopes=["read"])).json()
    cid = await _channel_with_bot(client, alice, bot["id"])
    await client.post(f"/channels/{cid}/invite", headers=alice["headers"],
                      json={"user_id": plain["id"]})
    mid = (await client.post(f"/channels/{cid}/messages", headers=alice["headers"],
                             json={"content": "react to me"})).json()["id"]

    ok = await client.post(f"/channels/{cid}/messages/{mid}/reactions",
                           headers=_auth(bot["token"]), json={"emoji": "👍"})
    assert ok.status_code in (200, 201), ok.text
    no = await client.post(f"/channels/{cid}/messages/{mid}/reactions",
                           headers=_auth(plain["token"]), json={"emoji": "👍"})
    assert no.status_code == 403


async def test_unlisted_endpoints_are_denied_even_with_every_scope(client, alice, bob):
    """The allowlist is the boundary, not the scopes. A bot holding all three
    still cannot do things no scope was ever meant to grant."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice, scopes=["read", "write", "react"])).json()
    h = _auth(bot["token"])

    # Creating channels, joining on its own initiative, opening DMs, searching
    # for people, and moderating are all absent from BOT_ROUTES.
    assert (await client.post("/channels", headers=h,
                              json={"slug": "botmade", "name": "botmade"})).status_code == 403
    assert (await client.post("/dms", headers=h, json={"user_id": bob["id"]})).status_code == 403
    assert (await client.get("/users/search?q=al", headers=h)).status_code == 403
    assert (await client.get("/users/online", headers=h)).status_code == 403

    cid = await _channel_with_bot(client, alice, bot["id"])
    assert (await client.post(f"/channels/{cid}/kick", headers=h,
                              json={"user_id": bob["id"]})).status_code == 403
    assert (await client.post(f"/channels/{cid}/invite", headers=h,
                              json={"user_id": bob["id"]})).status_code == 403


async def test_identity_needs_no_scope(client, alice):
    """A write-only bot must still be able to learn who it is."""
    await _make_admin(alice["id"])
    bot = (await _new_bot(client, alice, scopes=[])).json()
    me = await client.get("/users/me", headers=_auth(bot["token"]))
    assert me.status_code == 200
    assert me.json()["id"] == bot["id"]


async def test_humans_are_untouched_by_the_allowlist(client, alice, bob):
    """Scopes narrow programs, not people. A human keeps the run of the API."""
    assert (await client.get("/users/search?q=b", headers=alice["headers"])).status_code == 200
    assert (await client.get("/users/online", headers=alice["headers"])).status_code == 200
    import uuid
    slug = "hm" + uuid.uuid4().hex[:8]  # slugs are globally unique
    made = await client.post("/channels", headers=alice["headers"],
                             json={"slug": slug, "name": slug})
    assert made.status_code == 201, made.text
