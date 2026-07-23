"""Test the 2-channel-per-non-admin creation limit."""
import json
import time
import urllib.error
import urllib.request

B = "http://localhost:8000"


def login(u):
    d = json.dumps({"username_or_email": u, "password": "password123"}).encode()
    r = urllib.request.Request(B + "/auth/login", data=d, headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(r, timeout=8).read())["access_token"]


def api(path, tok, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method,
                               headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        raw = urllib.request.urlopen(r, timeout=8).read()
        return urllib.request.urlopen  # placeholder, not used
    except urllib.error.HTTPError as e:
        return e.code


def call(path, tok, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(B + path, data=data, method=method,
                               headers={"Content-Type": "application/json", "Authorization": f"Bearer {tok}"})
    try:
        raw = urllib.request.urlopen(r, timeout=8).read()
        return 200 if not raw else json.loads(raw)
    except urllib.error.HTTPError as e:
        return e.code


def create(tok, slug, private=False):
    return call("/channels", tok, "POST",
                {"slug": slug, "name": slug, "topic": "", "is_private": private})


bob = login("bob")
bob_id = None
alice = login("alice")

# Reset bob's created channels to 0
me_bob = call("/users/me", bob)["id"]
chans = call("/channels", bob)
for c in chans:
    if c.get("created_by") == me_bob and c["kind"] != "dm":
        call(f"/channels/{c['id']}", bob, "DELETE")

sfx = int(time.time())
print("=== non-admin (bob) limited to 2 ===")
r1 = create(bob, f"lim-{sfx}-1")
r2 = create(bob, f"lim-{sfx}-2")
r3 = create(bob, f"lim-{sfx}-3")
print("create #1:", "id" in r1 if isinstance(r1, dict) else r1)
print("create #2:", "id" in r2 if isinstance(r2, dict) else r2)
print("create #3 (should be 403):", r3)
print("PASS first two succeed:", isinstance(r1, dict) and isinstance(r2, dict))
print("PASS third blocked (403):", r3 == 403)

# delete one -> can create again
call(f"/channels/{r1['id']}", bob, "DELETE")
r4 = create(bob, f"lim-{sfx}-4")
print("PASS after deleting one, can create again:", isinstance(r4, dict))

# cleanup bob's
for c in call("/channels", bob):
    if c.get("created_by") == me_bob and c["kind"] != "dm":
        call(f"/channels/{c['id']}", bob, "DELETE")

print("\n=== admin (alice) is unlimited ===")
made = [create(alice, f"admin-{sfx}-{i}") for i in range(3)]
print("PASS admin created 3+:", all(isinstance(m, dict) for m in made))
for m in made:
    if isinstance(m, dict):
        call(f"/channels/{m['id']}", alice, "DELETE")
print("DONE")
