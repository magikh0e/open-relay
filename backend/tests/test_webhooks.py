"""Incoming webhooks: creation gating, invoke posts a message, token auth."""
from uuid import uuid4

import pytest

pytestmark = pytest.mark.asyncio


async def _channel(client, owner):
    slug = "wh" + uuid4().hex[:8]
    res = await client.post(
        "/channels",
        headers=owner["headers"],
        json={"slug": slug, "name": "hooks", "is_private": False},
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


async def test_create_and_invoke_posts_a_message(client, alice):
    cid = await _channel(client, alice)

    res = await client.post(
        f"/channels/{cid}/webhooks", headers=alice["headers"], json={"name": "CI"}
    )
    assert res.status_code == 201, res.text
    wh = res.json()
    assert wh["name"] == "CI" and "/api/webhooks/" in wh["url"]
    wid, token = wh["id"], wh["url"].rsplit("/", 1)[-1]

    # Fire it with only the secret URL (no auth header).
    fire = await client.post(f"/webhooks/{wid}/{token}", json={"text": "build passed"})
    assert fire.status_code == 200, fire.text

    msgs = (
        await client.get(f"/channels/{cid}/messages", headers=alice["headers"])
    ).json()
    posted = msgs[-1]
    assert posted["content"] == "build passed"
    assert posted["sender"] is None
    assert posted["author_name"] == "CI"

    # Per-post name override.
    await client.post(f"/webhooks/{wid}/{token}", json={"text": "hi", "name": "Deploy"})
    msgs = (
        await client.get(f"/channels/{cid}/messages", headers=alice["headers"])
    ).json()
    assert msgs[-1]["author_name"] == "Deploy"


async def test_bad_token_is_404(client, alice):
    cid = await _channel(client, alice)
    wid = (
        await client.post(
            f"/channels/{cid}/webhooks", headers=alice["headers"], json={"name": "CI"}
        )
    ).json()["id"]
    fire = await client.post(f"/webhooks/{wid}/wrong-token", json={"text": "x"})
    assert fire.status_code == 404


async def test_non_moderator_cannot_create(client, alice, bob):
    cid = await _channel(client, alice)
    res = await client.post(
        f"/channels/{cid}/webhooks", headers=bob["headers"], json={"name": "X"}
    )
    assert res.status_code == 403
