"""Test /invite (add to private channel) + /away status."""
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
        return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        return e.code


alice = login("alice")
bob_id = api("/users/search?q=bob", alice)[0]["id"]
alice_id = api("/users/me", alice)["id"]
sfx = int(time.time())

ch = api("/channels", alice, "POST", {"slug": f"priv-{sfx}", "name": "privtest", "topic": "", "is_private": True})
cid = ch["id"]

print("=== invite ===")
before = [m["username"] for m in api(f"/channels/{cid}/members", alice)]
api(f"/channels/{cid}/invite", alice, "POST", {"user_id": bob_id})
after = [m["username"] for m in api(f"/channels/{cid}/members", alice)]
print("PASS bob invited to private channel:", "bob" in after and "bob" not in before)
hist = api(f"/channels/{cid}/messages", alice)
print("PASS invite announced:", any(m["content"].startswith("/me ") and "added @bob" in m["content"] for m in hist))
api(f"/channels/{cid}/ban", alice, "POST", {"user_id": bob_id})
print("PASS banned user invite rejected (403):", api(f"/channels/{cid}/invite", alice, "POST", {"user_id": bob_id}) == 403)
api(f"/channels/{cid}", alice, "DELETE")

print("=== away ===")
api("/users/away", alice, "POST", {"message": "out for lunch"})
print("PASS away set:", api("/users/away", alice).get(alice_id) == "out for lunch")
api("/users/away", alice, "POST", {"message": ""})
print("PASS away cleared:", alice_id not in api("/users/away", alice))
print("DONE")
