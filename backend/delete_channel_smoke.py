"""Test channel deletion: owner + admin can, others can't, DMs can't, audited."""
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


def mkchan(token, name):
    return req("POST", "/channels", token, {"slug": name, "name": name, "topic": "", "is_private": False})[1]


alice = login("alice")   # site admin
bob = login("bob")
bob_id = req("GET", "/users/search?q=bob", alice)[1][0]["id"]

print("=== owner can delete own channel ===")
c1 = mkchan(bob, f"del-{SFX}-a")
req("POST", f"/channels/{c1['id']}/messages", bob, {"content": "hi"})
code, _ = req("DELETE", f"/channels/{c1['id']}", bob)
gone, _ = req("GET", f"/channels/{c1['id']}", bob)
print(f"owner delete -> {code}, channel now -> {gone}")
print("PASS owner deletes own channel:", code == 204 and gone == 404)

print("\n=== admin can delete another's channel ===")
c2 = mkchan(bob, f"del-{SFX}-b")
code, _ = req("DELETE", f"/channels/{c2['id']}", alice)   # alice not a member, but admin
print(f"admin delete -> {code}")
print("PASS admin deletes any channel:", code == 204)

print("\n=== non-owner member cannot delete ===")
c3 = mkchan(alice, f"del-{SFX}-c")   # alice owner
req("POST", f"/channels/{c3['id']}/join", bob)  # bob is a plain member
code, _ = req("DELETE", f"/channels/{c3['id']}", bob)
print(f"member delete -> {code}")
print("PASS non-owner member blocked (403):", code == 403)
req("DELETE", f"/channels/{c3['id']}", alice)  # cleanup

print("\n=== DMs cannot be deleted ===")
dm = req("POST", "/dms", alice, {"user_id": bob_id})[1]
code, _ = req("DELETE", f"/channels/{dm['id']}", alice)
print(f"dm delete -> {code}")
print("PASS DM deletion rejected (400):", code == 400)

print("\n=== deletion is audited ===")
_, audit = req("GET", "/moderation/audit", alice)
dels = [a for a in audit if a["action"] == "channel.delete"]
print("recent channel.delete entries:", [a["detail"] for a in dels[:3]])
print("PASS deletions recorded in audit:", len(dels) >= 2)
