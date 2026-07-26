"""Group key epochs: distribution, access, rotation and the rules around them.

The server never sees a group key, so these tests do not exercise real crypto.
They pin the parts the server is actually responsible for: that a key epoch is
only published by someone entitled to, that it covers exactly the right people,
that members can only fetch their own sealed copy, and that epochs advance
monotonically so a rotation genuinely supersedes what came before.
"""
import pytest

pytestmark = pytest.mark.asyncio

# Stand-ins for real key material. The server treats these as opaque, which is
# precisely the property under test.
FAKE_PUB = "BFAKEPUBLICKEYbase64=="


def _shares(*user_ids):
    return [
        {
            "user_id": uid,
            "wrapped_key": f"sealed-for-{uid}",
            "sender_public_key": FAKE_PUB,
        }
        for uid in user_ids
    ]


async def _with_keys(client, *users):
    """Give each user a published public key, as enabling E2EE would."""
    for u in users:
        r = await client.put(
            "/keys/me",
            headers=u["headers"],
            json={
                "public_key": FAKE_PUB,
                "wrapped_private_key": "wrapped",
                "salt": "c2FsdA==",
                "iv": "aXY=",
            },
        )
        assert r.status_code in (200, 201), r.text


async def _group(client, owner, member_ids, name="Secret"):
    r = await client.post(
        "/dms/group", json={"user_ids": member_ids, "name": name},
        headers=owner["headers"],
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# --- publishing ------------------------------------------------------------

async def test_owner_publishes_first_epoch(client, alice, bob, user_factory):
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    r = await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["current_epoch"] == 1
    assert [k["epoch"] for k in body["keys"]] == [1]


async def test_shares_must_cover_exactly_the_members(client, alice, bob, user_factory):
    carol = await user_factory()
    dave = await user_factory()
    await _with_keys(client, alice, bob, carol, dave)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    # Missing a member would silently lock them out of the conversation.
    missing = await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"])},
    )
    assert missing.status_code == 400

    # A share for someone outside the group is equally wrong.
    extra = await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"], dave["id"])},
    )
    assert extra.status_code == 400


async def test_rejects_members_without_encryption_set_up(client, alice, bob, user_factory):
    """A member with no published key could not have been sealed a share."""
    carol = await user_factory()  # deliberately no key
    await _with_keys(client, alice, bob)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    r = await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
    )
    assert r.status_code == 400
    assert "encryption" in r.json()["detail"].lower()


async def test_only_the_owner_may_publish(client, alice, bob, user_factory):
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    r = await client.post(
        f"/dms/{gid}/keys",
        headers=bob["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
    )
    assert r.status_code == 403


async def test_duplicate_share_for_one_member_is_rejected(client, alice, bob, user_factory):
    """Two copies for one member and none for another would pass a naive count
    check while leaving somebody unable to read."""
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    doubled = _shares(alice["id"], bob["id"], bob["id"])
    r = await client.post(f"/dms/{gid}/keys", headers=alice["headers"], json={"shares": doubled})
    assert r.status_code == 400
    assert "duplicate" in r.json()["detail"].lower()

    # And nothing was stored: the group is still plaintext.
    keys = (await client.get(f"/dms/{gid}/keys", headers=alice["headers"])).json()
    assert keys["current_epoch"] is None


# --- fetching --------------------------------------------------------------

async def test_each_member_gets_only_their_own_share(client, alice, bob, user_factory):
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
    )

    for u in (alice, bob, carol):
        r = await client.get(f"/dms/{gid}/keys", headers=u["headers"])
        assert r.status_code == 200, r.text
        keys = r.json()["keys"]
        assert len(keys) == 1
        # Sealed to them specifically, and nobody else's copy is returned.
        assert keys[0]["wrapped_key"] == f"sealed-for-{u['id']}"


async def test_non_member_cannot_fetch_keys(client, alice, bob, user_factory):
    carol = await user_factory()
    dave = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    await client.post(
        f"/dms/{gid}/keys",
        headers=alice["headers"],
        json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
    )

    assert (await client.get(f"/dms/{gid}/keys", headers=dave["headers"])).status_code == 403


async def test_plaintext_group_reports_no_epoch(client, alice, bob, user_factory):
    carol = await user_factory()
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    r = await client.get(f"/dms/{gid}/keys", headers=alice["headers"])
    assert r.status_code == 200
    assert r.json() == {"keys": [], "current_epoch": None}


# --- rotation --------------------------------------------------------------

async def test_epochs_advance_and_history_is_retained(client, alice, bob, user_factory):
    """A member present for both epochs keeps the older key, so the stretch of
    history encrypted under it stays readable."""
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    for _ in range(2):
        r = await client.post(
            f"/dms/{gid}/keys",
            headers=alice["headers"],
            json={"shares": _shares(alice["id"], bob["id"], carol["id"])},
        )
        assert r.status_code == 201, r.text

    r = await client.get(f"/dms/{gid}/keys", headers=bob["headers"])
    body = r.json()
    assert body["current_epoch"] == 2
    # Oldest first, so a client can walk history in order.
    assert [k["epoch"] for k in body["keys"]] == [1, 2]


async def test_epoch_is_server_assigned_not_client_supplied(client, alice, bob, user_factory):
    """Two publishes must not be able to collide on, or replay, an epoch."""
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])

    payload = {
        "shares": _shares(alice["id"], bob["id"], carol["id"]),
        # A client trying to pin the epoch is simply ignored.
        "epoch": 99,
    }
    first = await client.post(f"/dms/{gid}/keys", headers=alice["headers"], json=payload)
    second = await client.post(f"/dms/{gid}/keys", headers=alice["headers"], json=payload)
    assert first.json()["current_epoch"] == 1
    assert second.json()["current_epoch"] == 2


async def test_group_size_cap_bounds_the_share_list(client, alice, user_factory):
    """The share list cannot exceed the group cap, so a rekey cannot smuggle in
    more recipients than a group may hold."""
    gid_owner = alice
    others = [await user_factory() for _ in range(3)]
    await _with_keys(client, alice, *others)
    gid = await _group(client, gid_owner, [u["id"] for u in others])

    oversized = _shares(*([alice["id"]] + [u["id"] for u in others]))
    oversized += _shares(*[f"00000000-0000-0000-0000-{i:012d}" for i in range(25)])
    r = await client.post(f"/dms/{gid}/keys", headers=alice["headers"], json={"shares": oversized})
    assert r.status_code == 422  # schema rejects it before any of it is stored
