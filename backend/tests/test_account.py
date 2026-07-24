"""Self-service export and account deletion, plus jump-to-message."""
import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import PushSubscription, User
from app.redis_client import redis_client

pytestmark = pytest.mark.asyncio


async def _dm(client, a, b):
    res = await client.post("/dms", headers=a["headers"], json={"user_id": b["id"]})
    return res.json()["id"]


async def test_export_includes_your_messages(client, alice, bob):
    cid = await _dm(client, alice, bob)
    await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "export me please"},
    )
    res = await client.get("/users/me/export", headers=alice["headers"])
    assert res.status_code == 200
    body = res.json()
    assert body["account"]["username"] == alice["username"]
    assert any(m["content"] == "export me please" for m in body["messages"])


async def test_export_marks_encrypted_messages_as_such(client, alice, bob):
    """The export can't decrypt — it must say so rather than look like plaintext."""
    cid = await _dm(client, alice, bob)
    await client.post(
        f"/channels/{cid}/messages",
        headers=alice["headers"],
        json={"content": "Y2lwaGVydGV4dA==", "encrypted": True},
    )
    body = (await client.get("/users/me/export", headers=alice["headers"])).json()
    enc = [m for m in body["messages"] if m["encrypted"]]
    assert enc, "encrypted message missing from export"
    assert enc[0]["content"] == "Y2lwaGVydGV4dA=="


async def test_delete_requires_correct_password(client, alice):
    res = await client.post(
        "/users/me/delete", headers=alice["headers"], json={"password": "wrong"}
    )
    assert res.status_code == 403


async def test_delete_removes_the_account(client, user_factory):
    user = await user_factory()
    res = await client.post(
        "/users/me/delete",
        headers=user["headers"],
        json={"password": "password123"},
    )
    assert res.status_code == 204

    # Token no longer resolves, and the row is gone.
    assert (await client.get("/users/me", headers=user["headers"])).status_code == 401
    async with SessionLocal() as db:
        assert await db.get(User, user["id"]) is None


async def test_delete_takes_push_subscriptions_with_it(client, user_factory):
    user = await user_factory()
    await client.post(
        "/push/subscribe",
        headers=user["headers"],
        json={
            "endpoint": f"https://push.example/{user['id']}",
            "p256dh": "key",
            "auth": "auth",
        },
    )
    async with SessionLocal() as db:
        before = (
            await db.execute(
                select(PushSubscription).where(
                    PushSubscription.user_id == user["id"]
                )
            )
        ).scalars().all()
    assert before, "subscription was not stored"

    await client.post(
        "/users/me/delete",
        headers=user["headers"],
        json={"password": "password123"},
    )
    async with SessionLocal() as db:
        after = (
            await db.execute(
                select(PushSubscription).where(
                    PushSubscription.user_id == user["id"]
                )
            )
        ).scalars().all()
    assert after == [], "push subscriptions outlived the account"


# --- jump to message ------------------------------------------------------

async def test_around_returns_a_window_of_context(client, alice, bob):
    cid = await _dm(client, alice, bob)
    ids = []
    for i in range(20):
        # Posting 20 in a row would otherwise trip the per-user send throttle.
        await redis_client.delete(f"rl:msg:{alice['id']}")
        res = await client.post(
            f"/channels/{cid}/messages",
            headers=alice["headers"],
            json={"content": f"msg {i}"},
        )
        assert res.status_code == 201, res.text
        ids.append(res.json()["id"])
    target = ids[5]

    res = await client.get(
        f"/channels/{cid}/messages?around={target}&limit=10",
        headers=alice["headers"],
    )
    assert res.status_code == 200
    window = res.json()
    returned = [m["id"] for m in window]
    assert target in returned, "target message missing from its own window"
    # Context on both sides, in chronological order.
    assert returned.index(target) > 0, "no older context"
    assert returned.index(target) < len(returned) - 1, "no newer context"
    times = [m["created_at"] for m in window]
    assert times == sorted(times)


async def test_around_rejects_a_foreign_message(client, alice, bob):
    cid = await _dm(client, alice, bob)
    res = await client.get(
        f"/channels/{cid}/messages?around=00000000-0000-0000-0000-000000000000",
        headers=alice["headers"],
    )
    assert res.status_code == 404


# --- push subscriptions ---------------------------------------------------

async def test_push_key_is_available(client):
    res = await client.get("/push/key")
    assert res.status_code == 200
    assert len(res.json()["public_key"]) > 20


async def test_subscribing_twice_updates_rather_than_duplicates(client, alice):
    payload = {
        "endpoint": f"https://push.example/dup-{alice['id']}",
        "p256dh": "one",
        "auth": "a",
    }
    assert (
        await client.post("/push/subscribe", headers=alice["headers"], json=payload)
    ).status_code == 204
    payload["p256dh"] = "two"
    assert (
        await client.post("/push/subscribe", headers=alice["headers"], json=payload)
    ).status_code == 204

    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(PushSubscription).where(
                    PushSubscription.endpoint == payload["endpoint"]
                )
            )
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].p256dh == "two"
