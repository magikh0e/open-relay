"""Seed a clean, realistic conversation for documentation screenshots."""
import httpx

B = "http://localhost:8000"


def clear_limits():
    import subprocess
    subprocess.run(
        ["docker", "exec", "chat-app-redis-1", "sh", "-c",
         "redis-cli --scan --pattern 'register:*' | xargs -r redis-cli del; "
         "redis-cli --scan --pattern 'rl:msg:*' | xargs -r redis-cli del"],
        capture_output=True,
    )


def login(u, p="password123"):
    r = httpx.post(f"{B}/auth/login", json={"username_or_email": u, "password": p})
    return r.json()["access_token"] if r.status_code == 200 else None


def register(u, email, display):
    r = httpx.post(f"{B}/auth/register", json={
        "username": u, "email": email, "password": "password123",
        "display_name": display,
    })
    if r.status_code == 201:
        return r.json()["access_token"]
    return login(u)


def h(tok):
    return {"Authorization": f"Bearer {tok}"}


def me(tok):
    return httpx.get(f"{B}/users/me", headers=h(tok)).json()["id"]


def post(tok, cid, content, reply_to=None):
    body = {"content": content}
    if reply_to:
        body["reply_to_id"] = reply_to
    clear_limits()
    return httpx.post(f"{B}/channels/{cid}/messages", headers=h(tok), json=body).json()


def react(tok, cid, mid, emoji):
    httpx.post(f"{B}/channels/{cid}/messages/{mid}/reactions",
               headers=h(tok), json={"emoji": emoji})


if __name__ == "__main__":
    clear_limits()
    # register() falls back to login when the account already exists, so this
    # seeds a clean database and is safe to re-run against a populated one.
    alice = register("alice", "alice@example.com", "Alice")
    bob = register("bob", "bob@example.com", "Bob")
    mia = register("mchen", "mchen@example.com", "Mia Chen")
    devon = register("dpark", "dpark@example.com", "Devon Park")

    # Update profiles so the screenshots read naturally.
    httpx.patch(f"{B}/users/me", headers=h(mia),
                json={"pronouns": "she/her", "bio": "No-till grower, LAB evangelist."})
    httpx.patch(f"{B}/users/me", headers=h(devon),
                json={"pronouns": "they/them", "bio": "First season under lights 🌱"})

    # A public channel for the demo.
    clear_limits()
    ch = httpx.post(f"{B}/channels", headers=h(alice), json={
        "slug": "garden", "name": "garden",
        "topic": "Grow logs, KNF recipes & harvest talk",
        "is_private": False,
    }).json()
    cid = ch["id"]

    for tok in (bob, mia, devon):
        httpx.post(f"{B}/channels/{cid}/join", headers=h(tok))

    alice_un = "alice"
    # A realistic exchange that shows mentions, replies, formatting, code and reactions.
    post(mia, cid, "morning! how did the FPJ turn out? 🫧")
    m1 = post(alice, cid,
              "strong batch — stuck to the **1:1** plant-to-sugar ratio and it was "
              "bubbling by day two")
    post(alice, cid,
         "full recipe for anyone starting one:\n"
         "```\nFPJ — Fermented Plant Juice\n"
         "• fast-growing tips : brown sugar = 1 : 1 by weight\n"
         "• pack loosely, ferment 5–7 days in the shade\n"
         "• strain, then dilute 1:500 before foliar feeding\n```")
    q = post(devon, cid, f"@{alice_un} this is great — mind sharing your LAB recipe too?")
    r = post(alice, cid,
             "rice-wash water first, then milk at `1:10`. I'll write the full thing "
             "up in a thread later today", reply_to=q["id"])
    post(bob, cid, "saving all of this 🔥")

    react(bob, cid, m1["id"], "🔥")
    react(mia, cid, m1["id"], "🌱")
    react(devon, cid, r["id"], "🙏")

    # --- encrypted DM between Alice and Bob (for the encryption screenshot) ------
    # Keys are generated in the browser, so the demo screenshot script sets those
    # up client-side; here we just make sure the DM channel exists.
    bob_id = me(bob)
    httpx.post(f"{B}/dms", headers=h(alice), json={"user_id": bob_id})

    # A second public channel so the sidebar reads like an established space.
    clear_limits()
    gen = httpx.post(f"{B}/channels", headers=h(alice), json={
        "slug": "general", "name": "general",
        "topic": "Anything and everything", "is_private": False,
    }).json()
    if "id" in gen:
        for tok in (bob, mia, devon):
            httpx.post(f"{B}/channels/{gen['id']}/join", headers=h(tok))

    # --- a group DM, for the group-chat screenshot ----------------------------
    grp = httpx.post(f"{B}/dms/group", headers=h(alice), json={
        "user_ids": [me(bob), me(mia), me(devon)],
        "name": "Harvest Planning",
    }).json()
    gid = grp["id"]

    def gpost(tok, text):
        clear_limits()
        httpx.post(f"{B}/channels/{gid}/messages", headers=h(tok),
                   json={"content": text})

    gpost(alice, "made a group so we're not clogging #garden with logistics")
    gpost(mia, "perfect. who's got curing space this round?")
    gpost(devon, "i can take a few jars, got a spare closet 🙌")
    gpost(bob, "i'll bring the boveda packs")
    gpost(alice, "🙏 sending the schedule tonight")

    print("CHANNEL_ID", cid, "GROUP_ID", gid)
    print("done")
