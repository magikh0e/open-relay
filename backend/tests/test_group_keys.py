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


# --- sending encrypted group messages --------------------------------------

async def _publish(client, owner, gid, *user_ids):
    r = await client.post(
        f"/dms/{gid}/keys", headers=owner["headers"], json={"shares": _shares(*user_ids)}
    )
    assert r.status_code == 201, r.text
    return r.json()["current_epoch"]


async def test_encrypted_group_message_records_its_epoch(client, alice, bob, user_factory):
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    epoch = await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])

    r = await client.post(
        f"/channels/{gid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True, "key_epoch": epoch},
    )
    assert r.status_code == 201, r.text
    assert r.json()["encrypted"] is True
    assert r.json()["key_epoch"] == epoch


async def test_encrypted_group_message_requires_a_key(client, alice, bob, user_factory):
    """A group with no published key cannot accept ciphertext nobody can open."""
    carol = await user_factory()
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    r = await client.post(
        f"/channels/{gid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True, "key_epoch": 1},
    )
    assert r.status_code == 400


async def test_encrypted_group_message_must_name_its_epoch(client, alice, bob, user_factory):
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])

    r = await client.post(
        f"/channels/{gid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True},
    )
    assert r.status_code == 400


async def test_stale_epoch_is_refused_rather_than_stored(client, alice, bob, user_factory):
    """Sending under a superseded key would be unreadable to anyone who has
    already rotated, so the write is refused and the client told to re-fetch."""
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    stale = await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])
    await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])  # now epoch 2

    r = await client.post(
        f"/channels/{gid}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True, "key_epoch": stale},
    )
    assert r.status_code == 409


async def test_public_channels_still_refuse_ciphertext(client, alice):
    """Groups gained encryption; ordinary channels deliberately did not."""
    slug = "pub" + alice["id"][:8]
    ch = await client.post(
        "/channels",
        headers=alice["headers"],
        json={"slug": slug, "name": slug, "is_private": False},
    )
    assert ch.status_code == 201, ch.text
    r = await client.post(
        f"/channels/{ch.json()['id']}/messages",
        headers=alice["headers"],
        json={"content": "AAAABBBBCCCC", "encrypted": True},
    )
    assert r.status_code == 400


# --- rotation is enforced, not merely encouraged ---------------------------

async def test_send_blocked_until_the_key_follows_a_removal(client, alice, bob, user_factory):
    """Removing someone without rotating leaves them holding a working key.
    The server refuses further encrypted writes until the key catches up."""
    carol = await user_factory()
    await _with_keys(client, alice, bob, carol)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    epoch = await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])

    # Works while the key matches the membership.
    ok = await client.post(
        f"/channels/{gid}/messages", headers=alice["headers"],
        json={"content": "AAAA", "encrypted": True, "key_epoch": epoch},
    )
    assert ok.status_code == 201

    await client.delete(f"/dms/{gid}/members/{carol['id']}", headers=alice["headers"])

    # Carol is gone but still holds epoch 1, so writing under it is refused.
    blocked = await client.post(
        f"/channels/{gid}/messages", headers=alice["headers"],
        json={"content": "BBBB", "encrypted": True, "key_epoch": epoch},
    )
    assert blocked.status_code == 409
    assert "rotat" in blocked.json()["detail"].lower()

    # Rotating to the reduced membership restores sending.
    new_epoch = await _publish(client, alice, gid, alice["id"], bob["id"])
    after = await client.post(
        f"/channels/{gid}/messages", headers=alice["headers"],
        json={"content": "CCCC", "encrypted": True, "key_epoch": new_epoch},
    )
    assert after.status_code == 201


async def test_send_blocked_until_the_key_follows_an_addition(client, alice, bob, user_factory):
    """A member added without a rotation holds no key at all, so letting the
    group keep writing would simply exclude them."""
    carol = await user_factory()
    dave = await user_factory()
    await _with_keys(client, alice, bob, carol, dave)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    epoch = await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])

    await client.post(
        f"/dms/{gid}/members", headers=alice["headers"], json={"user_id": dave["id"]}
    )
    blocked = await client.post(
        f"/channels/{gid}/messages", headers=alice["headers"],
        json={"content": "AAAA", "encrypted": True, "key_epoch": epoch},
    )
    assert blocked.status_code == 409

    new_epoch = await _publish(
        client, alice, gid, alice["id"], bob["id"], carol["id"], dave["id"]
    )
    after = await client.post(
        f"/channels/{gid}/messages", headers=alice["headers"],
        json={"content": "BBBB", "encrypted": True, "key_epoch": new_epoch},
    )
    assert after.status_code == 201


async def test_new_member_gets_no_share_for_earlier_epochs(client, alice, bob, user_factory):
    """The history cutoff: joining hands you the current key only."""
    carol = await user_factory()
    dave = await user_factory()
    await _with_keys(client, alice, bob, carol, dave)
    gid = await _group(client, alice, [bob["id"], carol["id"]])
    await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"])  # epoch 1

    await client.post(
        f"/dms/{gid}/members", headers=alice["headers"], json={"user_id": dave["id"]}
    )
    await _publish(client, alice, gid, alice["id"], bob["id"], carol["id"], dave["id"])

    daves = (await client.get(f"/dms/{gid}/keys", headers=dave["headers"])).json()
    assert [k["epoch"] for k in daves["keys"]] == [2]  # nothing from before he joined

    alices = (await client.get(f"/dms/{gid}/keys", headers=alice["headers"])).json()
    assert [k["epoch"] for k in alices["keys"]] == [1, 2]  # present throughout
