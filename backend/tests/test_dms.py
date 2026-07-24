"""DM lifecycle: closing is one-sided dismissal, not deletion."""
import pytest

pytestmark = pytest.mark.asyncio


async def _open(client, a, b):
    res = await client.post("/dms", headers=a["headers"], json={"user_id": b["id"]})
    assert res.status_code in (200, 201)
    return res.json()["id"]


async def _dm_ids(client, user):
    res = await client.get("/dms", headers=user["headers"])
    return [d["id"] for d in res.json()]


async def test_close_hides_only_for_you(client, alice, bob):
    cid = await _open(client, alice, bob)
    assert cid in await _dm_ids(client, alice)
    assert cid in await _dm_ids(client, bob)

    res = await client.delete(f"/dms/{cid}", headers=alice["headers"])
    assert res.status_code == 204

    assert cid not in await _dm_ids(client, alice)
    assert cid in await _dm_ids(client, bob), "closing must not affect the other person"


async def test_new_message_reopens_a_closed_dm(client, alice, bob):
    cid = await _open(client, alice, bob)
    await client.delete(f"/dms/{cid}", headers=alice["headers"])
    assert cid not in await _dm_ids(client, alice)

    await client.post(
        f"/channels/{cid}/messages", headers=bob["headers"], json={"content": "still here?"}
    )
    assert cid in await _dm_ids(client, alice)


async def test_reopening_restores_it(client, alice, bob):
    cid = await _open(client, alice, bob)
    await client.delete(f"/dms/{cid}", headers=alice["headers"])
    assert cid not in await _dm_ids(client, alice)

    await _open(client, alice, bob)
    assert cid in await _dm_ids(client, alice)


async def test_closing_keeps_the_history(client, alice, bob):
    cid = await _open(client, alice, bob)
    await client.post(
        f"/channels/{cid}/messages", headers=alice["headers"], json={"content": "remember this"}
    )
    await client.delete(f"/dms/{cid}", headers=alice["headers"])

    history = await client.get(f"/channels/{cid}/messages", headers=alice["headers"])
    assert history.status_code == 200
    assert any(m["content"] == "remember this" for m in history.json())


async def test_cannot_dm_yourself(client, alice):
    res = await client.post("/dms", headers=alice["headers"], json={"user_id": alice["id"]})
    assert res.status_code == 400
