"""Test setting a channel topic: permission, sanitize, live broadcast."""
import asyncio
import json
import urllib.error
import urllib.request

import websockets

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


async def main():
    alice = login("alice")
    bob = login("bob")
    _, chans = req("GET", "/channels", alice)
    cid = next(c for c in chans if c["slug"] == "general")["id"]
    req("POST", f"/channels/{cid}/join", bob)

    got = []
    async with websockets.connect(f"ws://localhost:8000/ws?token={bob}") as ws:
        task = asyncio.create_task(_collect(ws, got))
        await asyncio.sleep(0.4)

        # owner/admin sets a topic (with a zero-width char to test sanitizing)
        code, out = req("PATCH", f"/channels/{cid}", alice,
                        {"topic": "VPD talk​ & cloning"})
        print("set topic status:", code, "-> stored:", repr(out["topic"]))

        # non-owner member cannot
        code_bob, _ = req("PATCH", f"/channels/{cid}", bob, {"topic": "hacked"})

        await asyncio.sleep(0.5)
        await ws.close()
        await asyncio.gather(task, return_exceptions=True)

    upd = next((e for e in got if e["type"] == "channel_updated"), None)
    print("---")
    print("PASS topic set + sanitized (zero-width stripped):", out["topic"] == "VPD talk & cloning")
    print("PASS non-owner blocked (403):", code_bob == 403)
    print("PASS live channel_updated broadcast:", upd is not None and upd["data"]["topic"] == "VPD talk & cloning")


async def _collect(ws, out):
    try:
        async for raw in ws:
            out.append(json.loads(raw))
    except Exception:
        pass


asyncio.run(main())
