"""Test channel settings: privacy toggle + ownership transfer."""
import json
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
SFX = int(time.time())


def req(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers={"Content-Type": "application/json"})
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, None


def login(u):
    return req("POST", "/auth/login", body={"username_or_email": u, "password": "password123"})[1]["access_token"]


def roles(token, cid):
    _, members = req("GET", f"/channels/{cid}/members", token)
    return {m["username"]: m["role"] for m in members}


alice = login("alice")
bob = login("bob")
bob_id = req("GET", "/users/search?q=bob", alice)[1][0]["id"]
alice_id = req("GET", "/users/me", alice)[1]["id"]

ch = req("POST", "/channels", alice,
         {"slug": f"set-{SFX}", "name": "settest", "topic": "", "is_private": False})[1]
cid = ch["id"]
req("POST", f"/channels/{cid}/join", bob)

print("=== privacy toggle ===")
_, out = req("PATCH", f"/channels/{cid}", alice, {"is_private": True})
print("after is_private=true, kind:", out["kind"])
_, out2 = req("PATCH", f"/channels/{cid}", alice, {"is_private": False})
print("after is_private=false, kind:", out2["kind"])
print("PASS privacy toggles kind:", out["kind"] == "private" and out2["kind"] == "public")

print("\n=== ownership transfer ===")
before = roles(alice, cid)
code, _ = req("POST", f"/channels/{cid}/role", alice, {"user_id": bob_id, "role": "owner"})
after = roles(alice, cid)
print("before:", before, "| transfer status:", code, "| after:", after)
print("PASS bob now owner:", after.get("bob") == "owner")
print("PASS alice demoted to mod:", after.get("alice") == "mod")

print("\n=== transfer back (bob is owner now) ===")
code, _ = req("POST", f"/channels/{cid}/role", bob, {"user_id": alice_id, "role": "owner"})
final = roles(alice, cid)
print("after transfer back:", final)
print("PASS alice owner again:", final.get("alice") == "owner")

print("\n=== a mod cannot transfer ownership ===")
# bob is now mod; bob tries to make alice owner
code_mod, _ = req("POST", f"/channels/{cid}/role", bob, {"user_id": alice_id, "role": "owner"})
print("PASS mod cannot transfer (403):", code_mod == 403)

req("DELETE", f"/channels/{cid}", alice)  # cleanup
