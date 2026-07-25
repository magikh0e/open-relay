"""Plaintext group DMs: create, membership, access, messaging."""
import pytest

pytestmark = pytest.mark.asyncio


async def _group(client, owner, member_ids, name=None):
    body = {"user_ids": member_ids}
    if name:
        body["name"] = name
    return await client.post("/dms/group", json=body, headers=owner["headers"])


async def test_create_and_list_group(client, alice, bob, user_factory):
    carol = await user_factory()
    r = await _group(client, alice, [bob["id"], carol["id"]], name="Trip")
    assert r.status_code == 201, r.text
    g = r.json()
    assert g["kind"] == "group" and g["name"] == "Trip" and g["member_count"] == 3
    gid = g["id"]
    for u in (alice, bob, carol):
        dms = (await client.get("/dms", headers=u["headers"])).json()
        assert any(d["id"] == gid and d["kind"] == "group" for d in dms)


async def test_non_member_cannot_read(client, alice, bob, user_factory):
    carol = await user_factory()
    dave = await user_factory()
    gid = (await _group(client, alice, [bob["id"], carol["id"]])).json()["id"]
    assert (
        await client.get(f"/channels/{gid}", headers=dave["headers"])
    ).status_code == 403
    assert (
        await client.get(f"/channels/{gid}/messages", headers=dave["headers"])
    ).status_code in (403, 404)


async def test_members_can_message(client, alice, bob, user_factory):
    carol = await user_factory()
    gid = (await _group(client, alice, [bob["id"], carol["id"]])).json()["id"]
    r = await client.post(
        f"/channels/{gid}/messages",
        json={"content": "hi team"},
        headers=bob["headers"],
    )
    assert r.status_code == 201, r.text
    msgs = (
        await client.get(f"/channels/{gid}/messages", headers=carol["headers"])
    ).json()
    assert any(m["content"] == "hi team" for m in msgs)


async def test_owner_add_remove_member(client, alice, bob, user_factory):
    carol = await user_factory()
    dave = await user_factory()
    gid = (await _group(client, alice, [bob["id"], carol["id"]])).json()["id"]

    # A non-owner can't add.
    assert (
        await client.post(
            f"/dms/{gid}/members",
            json={"user_id": dave["id"]},
            headers=bob["headers"],
        )
    ).status_code == 403
    # The owner adds dave, who can then read.
    assert (
        await client.post(
            f"/dms/{gid}/members",
            json={"user_id": dave["id"]},
            headers=alice["headers"],
        )
    ).status_code == 204
    assert (
        await client.get(f"/channels/{gid}", headers=dave["headers"])
    ).status_code == 200
    # The owner removes dave, who loses access.
    assert (
        await client.delete(
            f"/dms/{gid}/members/{dave['id']}", headers=alice["headers"]
        )
    ).status_code == 204
    assert (
        await client.get(f"/channels/{gid}", headers=dave["headers"])
    ).status_code == 403


async def test_leave_group(client, alice, bob, user_factory):
    carol = await user_factory()
    gid = (await _group(client, alice, [bob["id"], carol["id"]])).json()["id"]
    assert (
        await client.post(f"/channels/{gid}/leave", headers=bob["headers"])
    ).status_code == 204
    assert (
        await client.get(f"/channels/{gid}", headers=bob["headers"])
    ).status_code == 403


async def test_group_needs_two_others(client, alice, bob):
    # One other person fails the schema's min_length before the handler runs.
    r = await client.post(
        "/dms/group", json={"user_ids": [bob["id"]]}, headers=alice["headers"]
    )
    assert r.status_code == 422


async def test_group_excluded_from_public_directory(client, alice, bob, user_factory):
    carol = await user_factory()
    gid = (await _group(client, alice, [bob["id"], carol["id"]])).json()["id"]
    listing = (await client.get("/channels", headers=alice["headers"])).json()
    assert all(c["id"] != gid for c in listing)
