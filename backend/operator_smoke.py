"""Test channel operator (mod) status: grant/revoke, permissions, audit."""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


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


def role_of(token, cid, username):
    _, members = req("GET", f"/channels/{cid}/members", token)
    return next((m["role"] for m in members if m["username"] == username), None)


alice = login("alice")   # owner of #general + admin
bob = login("bob")
_, chans = req("GET", "/channels", alice)
cid = next(c for c in chans if c["slug"] == "general")["id"]
bob_id = req("GET", "/users/search?q=bob", alice)[1][0]["id"]
alice_id = req("GET", "/users/me", alice)[1]["id"]
req("POST", f"/channels/{cid}/join", bob)

print("=== grant operator ===")
code, _ = req("POST", f"/channels/{cid}/role", alice, {"user_id": bob_id, "role": "mod"})
print("op bob ->", code, "| bob role now:", role_of(alice, cid, "bob"))
print("PASS bob is now op (mod):", code == 204 and role_of(alice, cid, "bob") == "mod")

print("\n=== an op (not owner/admin) cannot manage roles ===")
code, _ = req("POST", f"/channels/{cid}/role", bob, {"user_id": alice_id, "role": "member"})
print("PASS op cannot set roles (403):", code == 403)

print("\n=== guards ===")
c_self, _ = req("POST", f"/channels/{cid}/role", alice, {"user_id": alice_id, "role": "member"})
print("PASS can't change own role (400):", c_self == 400)
c_bad, _ = req("POST", f"/channels/{cid}/role", alice, {"user_id": bob_id, "role": "owner"})
print("PASS invalid role rejected (400):", c_bad == 400)

print("\n=== revoke operator ===")
code, _ = req("POST", f"/channels/{cid}/role", alice, {"user_id": bob_id, "role": "member"})
print("deop bob ->", code, "| bob role now:", role_of(alice, cid, "bob"))
print("PASS bob demoted to member:", code == 204 and role_of(alice, cid, "bob") == "member")

print("\n=== audited ===")
_, audit = req("GET", "/moderation/audit", alice)
actions = [a["action"] for a in audit[:6]]
print("recent audit actions:", actions)
print("PASS op/deop recorded:", "channel.op" in actions and "channel.deop" in actions)
