"""Test @mentions, profiles, and input sanitization (XSS defense-in-depth)."""
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
    with urllib.request.urlopen(r) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else None


def login(u):
    return req("POST", "/auth/login", body={"username_or_email": u, "password": "password123"})["access_token"]


alice = login("alice")
bob = login("bob")
me = req("GET", "/users/me", alice)
bob_id = req("GET", "/users/search?q=bob", alice)[0]["id"]
general = next(c for c in req("GET", "/channels", alice) if c["slug"] == "general")
cid = general["id"]

print("=== PROFILE ===")
# Update profile with messy input: zero-width space, control char (bell), tags.
prof = req("PATCH", "/users/me", alice, {
    "display_name": "  Alice  ",           # bell + surrounding spaces
    "bio": "Grower​ of plants\x07\n<b>bold?</b>",  # zero-width + bell + tag
    "pronouns": "she/her‮",                  # trailing bidi-override char
})
print("display_name:", repr(prof["display_name"]))
print("bio         :", repr(prof["bio"]))
print("pronouns    :", repr(prof["pronouns"]))

print("PASS display trimmed+stripped:", prof["display_name"] == "Alice")
print("PASS bio zero-width/ctrl removed, newline+tag kept:",
      prof["bio"] == "Grower of plants\n<b>bold?</b>")
print("PASS pronouns bidi-override stripped:", prof["pronouns"] == "she/her")

# Public profile fetch
pub = req("GET", f"/users/{bob_id}", alice)
print("PASS profile fetch:", pub["username"] == "bob" and "bio" in pub)

print("\n=== MENTIONS ===")
msg = req("POST", f"/channels/{cid}/messages", alice,
          {"content": "hey @Bob and @nobody, email bob@example.com"})
names = [m["username"] for m in msg["mentions"]]
print("mentions:", names)
print("PASS @Bob resolved case-insensitively:", "bob" in names)
print("PASS @nobody not resolved            :", "nobody" not in names)
print("PASS email not treated as mention    :", "example" not in names)

# History carries mentions
hist = req("GET", f"/channels/{cid}/messages", alice)
hm = next(m for m in hist if m["id"] == msg["id"])
print("PASS history has mention:", any(x["username"] == "bob" for x in hm["mentions"]))

print("\n=== XSS PAYLOAD IN MESSAGE ===")
xss = req("POST", f"/channels/{cid}/messages", alice,
          {"content": "<script>alert('xss')</script> ping @bob"})
# Content is preserved verbatim (angle brackets intact) — safety is at the
# React render layer (escaping), NOT by mangling the stored text.
print("stored content:", repr(xss["content"]))
print("PASS script text preserved (escaped at render, not stored-mangled):",
      xss["content"] == "<script>alert('xss')</script> ping @bob")
print("PASS mention still parsed alongside payload:",
      any(m["username"] == "bob" for m in xss["mentions"]))

# Empty-after-sanitize is rejected
bad = None
try:
    req("POST", f"/channels/{cid}/messages", alice, {"content": "​​"})
except urllib.error.HTTPError as e:
    bad = e.code
print("PASS all-zero-width message rejected:", bad in (400, 422))
