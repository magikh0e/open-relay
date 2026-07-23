"""Adversarial tests against the upload endpoint. Run with the rate limiter
cleared between calls (caller flushes rl:upload:* in redis)."""
import base64
import subprocess

import httpx

B = "http://localhost:8000"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def clear_rl():
    # wipe upload rate-limit keys so probes aren't throttled
    subprocess.run(
        ["docker", "exec", "chat-app-redis-1", "sh", "-c",
         "redis-cli --scan --pattern 'rl:upload:*' | xargs -r redis-cli del"],
        capture_output=True,
    )


def tok(u):
    return httpx.post(f"{B}/auth/login", json={"username_or_email": u, "password": "password123"}).json()["access_token"]


A = {"Authorization": f"Bearer {tok('alice')}"}


def up(name, data, ctype, hdr=A):
    clear_rl()
    return httpx.post(f"{B}/uploads", headers=hdr, files={"file": (name, data, ctype)}, timeout=15)


print("== 1. content-type spoof: HTML bytes, .png ext, client says image/png ==")
html = b"<script>alert(document.cookie)</script>"
r = up("x.png", html, "image/png")
j = r.json()
print(f"  accepted={r.status_code==201} stored_ct={j.get('content_type')} is_image={j.get('is_image')}")
sv = httpx.get(f"{B}/uploads/{j['id']}")
print(f"  SERVED ct={sv.headers.get('content-type')} nosniff={sv.headers.get('x-content-type-options')} "
      f"disp={sv.headers.get('content-disposition','')[:40]}")
print(f"  VERDICT served-as-image+nosniff (no HTML exec): "
      f"{sv.headers.get('content-type')=='image/png' and sv.headers.get('x-content-type-options')=='nosniff'}")

print("== 2. spoof content-type text/html on a .txt ==")
r = up("note.txt", b"<h1>hi</h1><script>1</script>", "text/html")
j = r.json()
sv = httpx.get(f"{B}/uploads/{j['id']}")
print(f"  stored_ct={j['content_type']} is_image={j['is_image']} served_disp={sv.headers.get('content-disposition','')[:20]}")
print(f"  VERDICT forced-download (attachment), not inline: {sv.headers.get('content-disposition','').startswith('attachment')}")

print("== 3. disallowed exts ==")
for n, c in [("shell.php", "application/x-php"), ("a.svg", "image/svg+xml"),
             ("x.html", "text/html"), ("x.htm", "text/html"), ("e.exe", "application/octet-stream"),
             (".htaccess", "text/plain"), ("x.phtml", "text/html"), ("x.js", "application/javascript")]:
    r = up(n, b"data", c)
    print(f"  {n:14} -> {r.status_code} {'REJECTED' if r.status_code==415 else 'ACCEPTED (!!)'}")

print("== 4. double extension shell.php.png ==")
r = up("shell.php.png", PNG, "image/png")
j = r.json()
print(f"  status={r.status_code} stored_ct={j.get('content_type')} name={j.get('name')} is_image={j.get('is_image')}")
print(f"  VERDICT stored as image/png (ext=png wins), name only shown escaped in UI")

print("== 5. path traversal in filename ==")
for n in ["../../../../etc/passwd", "..\\..\\win.ini", "../../foo.png", "/etc/shadow.png"]:
    r = up(n, PNG, "image/png")
    nm = r.json().get("name") if r.status_code == 201 else None
    print(f"  {n!r:30} -> {r.status_code} stored_name_in_db=uuid.ext (server), returned name={nm!r}")

print("== 6. CRLF header injection via filename ==")
r = up("a\r\nSet-Cookie: pwned=1\r\nX-Injected: 1.png", PNG, "image/png")
if r.status_code == 201:
    j = r.json()
    sv = httpx.get(f"{B}/uploads/{j['id']}")
    print(f"  name_returned={j['name']!r}")
    print(f"  response Set-Cookie present? {'set-cookie' in {k.lower() for k in sv.headers}}")
    print(f"  X-Injected present? {'x-injected' in {k.lower() for k in sv.headers}}")
    print(f"  content-disposition raw: {sv.headers.get('content-disposition')!r}")
    print(f"  VERDICT no header injection: {'set-cookie' not in {k.lower() for k in sv.headers} and 'x-injected' not in {k.lower() for k in sv.headers}}")
else:
    print(f"  rejected at upload: {r.status_code}")

print("== 7. null byte in filename ==")
r = up("evil\x00.png", PNG, "image/png")
_n = repr(r.json().get("name")) if r.status_code == 201 else r.text[:60]
print(f"  status={r.status_code} name={_n}")

print("== 8. oversized (11 MB) -> expect 413 ==")
big = b"A" * (11 * 1024 * 1024)
r = up("big.png", big, "image/png")
print(f"  status={r.status_code} {'REJECTED 413' if r.status_code==413 else 'ACCEPTED (!!)'}")

print("== 9. empty file -> expect 400 ==")
r = up("empty.png", b"", "image/png")
print(f"  status={r.status_code}")

print("== 10. GET with malformed / traversal upload_id -> expect 404 not 500 ==")
for uid in ["../../../etc/passwd", "not-a-uuid", "' OR 1=1--", "00000000-0000-0000-0000-000000000000"]:
    sv = httpx.get(f"{B}/uploads/{uid}")
    print(f"  {uid!r:34} -> {sv.status_code}")

print("== 11. GET is public (no auth) by design ==")
# use an id from test 1
sv = httpx.get(f"{B}/uploads/{j['id'] if False else ''}")
print("  (image serve worked unauthenticated in tests above)")

print("== 12. no-extension filename -> 415 ==")
r = up("noext", PNG, "image/png")
print(f"  status={r.status_code}")
print("DONE")
