"""Test login rate-limiting + admin audit log."""
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


alice = login("alice")   # admin
bob = login("bob")
_, chans = req("GET", "/channels", alice)
cid = next(c for c in chans if c["slug"] == "general")["id"]
bob_id = req("GET", "/users/search?q=bob", alice)[1][0]["id"]

print("=== AUDIT LOG ===")
req("POST", f"/channels/{cid}/join", bob)          # ensure member
req("POST", f"/channels/{cid}/kick", alice, {"user_id": bob_id})  # audited action

code, audit = req("GET", "/moderation/audit", alice)
print("audit fetch status:", code)
kick_entry = next((a for a in audit if a["action"] == "channel.kick" and a["target"] == "bob"), None)
print("latest kick entry:", kick_entry and {k: kick_entry[k] for k in ("actor", "action", "target")})
print("PASS kick recorded (actor=alice, target=bob):",
      kick_entry is not None and kick_entry["actor"] == "alice")

code_bob, _ = req("GET", "/moderation/audit", bob)
print("PASS non-admin blocked from audit (403):", code_bob == 403)

print("\n=== LOGIN RATE LIMIT (limit=10/min per identifier) ===")
codes = []
for i in range(12):
    c, _ = req("POST", "/auth/login", body={"username_or_email": "rltest", "password": "wrong"})
    codes.append(c)
first10 = codes[:10]
later = codes[10:]
print("first 10 codes:", first10)
print("later codes   :", later)
print("PASS first 10 are 401 (not throttled):", all(c == 401 for c in first10))
print("PASS throttled after limit (429):", 429 in later)
