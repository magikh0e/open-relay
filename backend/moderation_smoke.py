"""Test channel kick/ban/unban + member roles + admin flag."""
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


alice = login("alice")   # owner of #general + site admin
bob = login("bob")
_, chans = req("GET", "/channels", alice)
general = next(c for c in chans if c["slug"] == "general")
cid = general["id"]
bob_id = req("GET", "/users/search?q=bob", alice)[1][0]["id"]

# ensure bob is a member to start
req("POST", f"/channels/{cid}/join", bob)

print("=== roles + admin flag ===")
_, members = req("GET", f"/channels/{cid}/members", alice)
by = {m["username"]: m for m in members}
print("alice:", by["alice"]["role"], "is_admin=", by["alice"]["is_admin"])
print("bob  :", by["bob"]["role"], "is_admin=", by["bob"]["is_admin"])
print("PASS alice owner+admin:", by["alice"]["role"] == "owner" and by["alice"]["is_admin"])
print("PASS bob plain member :", by["bob"]["role"] == "member" and not by["bob"]["is_admin"])

print("\n=== permissions ===")
code, _ = req("POST", f"/channels/{cid}/kick", bob, {"user_id": by["alice"]["id"]})
print("PASS member cannot kick (403):", code == 403)
code, _ = req("POST", f"/channels/{cid}/kick", alice, {"user_id": by["alice"]["id"]})
print("PASS cannot kick self (400) :", code == 400)

print("\n=== kick ===")
code, _ = req("POST", f"/channels/{cid}/kick", alice, {"user_id": bob_id})
print("kick bob status:", code)
_, members = req("GET", f"/channels/{cid}/members", alice)
print("PASS bob removed:", not any(m["id"] == bob_id for m in members))
code, _ = req("POST", f"/channels/{cid}/join", bob)
print("PASS kicked bob can rejoin (public):", code == 200)

print("\n=== ban ===")
code, _ = req("POST", f"/channels/{cid}/ban", alice, {"user_id": bob_id, "reason": "spam"})
print("ban bob status:", code)
_, members = req("GET", f"/channels/{cid}/members", alice)
print("PASS bob removed by ban:", not any(m["id"] == bob_id for m in members))
code, _ = req("POST", f"/channels/{cid}/join", bob)
print("PASS banned bob CANNOT rejoin (403):", code == 403)

print("\n=== unban ===")
code, _ = req("POST", f"/channels/{cid}/unban", alice, {"user_id": bob_id})
print("unban status:", code)
code, _ = req("POST", f"/channels/{cid}/join", bob)
print("PASS unbanned bob can rejoin (200):", code == 200)
