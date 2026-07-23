"""Test username sanitization / hardening at registration."""
import json
import urllib.error
import urllib.request

BASE = "http://localhost:8000"


def register(username, email, password="password123"):
    body = {"username": username, "email": email, "password": password}
    data = json.dumps(body).encode()
    r = urllib.request.Request(BASE + "/auth/register", data=data,
                               headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


REJECTED = [
    ("<script>", "a@x.com"),          # markup
    ("bad name", "b@x.com"),          # space
    ("admin", "c@x.com"),             # reserved
    (".leading", "d@x.com"),          # starts with dot
    ("trailing-", "e@x.com"),         # ends with dash
    ("ab", "f@x.com"),                # too short
    ("a..b", "g@x.com"),              # repeated punctuation
    ("BOB", "h@x.com"),               # case-insensitive dup of existing 'bob'
    ("ＢＯＢ", "i@x.com"),               # fullwidth homoglyph of 'bob'
    ("nul\x00l", "j@x.com"),          # null byte
]

print("=== rejected (expect 4xx) ===")
allrej = True
for uname, email in REJECTED:
    code = register(uname, email)
    ok = code in (409, 422)
    allrej = allrej and ok
    print(f"  {uname!r:20} -> {code}  {'OK' if ok else 'FAIL'}")

print("=== accepted (expect 201) ===")
code = register("cool.grower-1", "grower1@x.com")
print(f"  'cool.grower-1'      -> {code}  {'OK' if code == 201 else '(already exists is fine on rerun)'}")

print("---")
print("PASS all malicious/invalid rejected:", allrej)
print("PASS valid accepted:", code in (201, 409))
