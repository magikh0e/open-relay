"""Test replying to a message over REST and live WebSocket."""
import asyncio
import json
import urllib.request

import websockets

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


async def main():
    alice = login("alice")
    bob = login("bob")
    general = next(c for c in req("GET", "/channels", bob) if c["slug"] == "general")
    cid = general["id"]

    bob_events = []
    stop = asyncio.Event()

    async def listen(ws):
        try:
            async for raw in ws:
                bob_events.append(json.loads(raw))
        except Exception:
            pass

    async with websockets.connect(f"ws://localhost:8000/ws?token={bob}") as bws:
        t = asyncio.create_task(listen(bws))
        await asyncio.sleep(0.4)

        # Alice posts a message; Bob replies to it.
        parent = req("POST", f"/channels/{cid}/messages", alice, {"content": "what's the VPD target?"})
        await asyncio.sleep(0.2)
        reply = req("POST", f"/channels/{cid}/messages", bob,
                    {"content": "1.2 kPa in flower", "reply_to_id": parent["id"]})
        print("reply.reply_to:", reply["reply_to"])

        # History carries the reply preview.
        await asyncio.sleep(0.3)
        hist = req("GET", f"/channels/{cid}/messages", alice)
        hm = next(m for m in hist if m["id"] == reply["id"])
        print("history reply_to:", hm["reply_to"])

        # Bad reply target is rejected.
        bad = None
        try:
            req("POST", f"/channels/{cid}/messages", bob,
                {"content": "x", "reply_to_id": "00000000-0000-0000-0000-000000000000"})
        except urllib.error.HTTPError as e:
            bad = e.code
        print("bad reply target status:", bad)

        await asyncio.sleep(0.4)
        stop.set()
        await bws.close()
        await asyncio.gather(t, return_exceptions=True)

    live_reply = next((e for e in bob_events if e["type"] == "message" and e["data"].get("reply_to")), None)
    print("---")
    print("PASS reply stored     :", reply["reply_to"] and reply["reply_to"]["id"] == parent["id"])
    print("PASS preview author   :", reply["reply_to"]["sender_name"] == "Alice")
    print("PASS preview content  :", reply["reply_to"]["content"] == "what's the VPD target?")
    print("PASS history has reply:", hm["reply_to"] is not None)
    print("PASS live broadcast   :", live_reply is not None)
    print("PASS bad target 400   :", bad == 400)


asyncio.run(main())
