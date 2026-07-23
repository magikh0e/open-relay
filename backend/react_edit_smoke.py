"""Test message editing + reactions over REST and live WebSocket."""
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
    channels = req("GET", "/channels", bob)
    general = next(c for c in channels if c["slug"] == "general")
    cid = general["id"]

    bob_events = []
    stop = asyncio.Event()

    async def listen(ws):
        try:
            async for raw in ws:
                bob_events.append(json.loads(raw))
                if stop.is_set():
                    return
        except Exception:
            pass

    async with websockets.connect(f"ws://localhost:8000/ws?token={bob}") as bws:
        t = asyncio.create_task(listen(bws))
        await asyncio.sleep(0.4)

        # 1. Alice posts
        msg = req("POST", f"/channels/{cid}/messages", alice, {"content": "orig text"})
        mid = msg["id"]
        print("posted:", msg["content"], "| reactions:", msg["reactions"])

        # 2. Alice edits
        await asyncio.sleep(0.3)
        edited = req("PATCH", f"/channels/{cid}/messages/{mid}", alice, {"content": "edited text"})
        print("edited ->", edited["content"], "| edited_at set:", edited["edited_at"] is not None)

        # 3. Reactions: bob 👍, alice 👍, bob removes 👍
        await asyncio.sleep(0.3)
        r1 = req("POST", f"/channels/{cid}/messages/{mid}/reactions", bob, {"emoji": "👍"})
        r2 = req("POST", f"/channels/{cid}/messages/{mid}/reactions", alice, {"emoji": "👍"})
        r3 = req("POST", f"/channels/{cid}/messages/{mid}/reactions", bob, {"emoji": "👍"})
        print(f"react counts: bob-add={r1['count']} alice-add={r2['count']} bob-remove={r3['count']}")

        # 4. History reflects edit + remaining reaction (alice's 👍)
        await asyncio.sleep(0.3)
        hist = req("GET", f"/channels/{cid}/messages", alice)
        hm = next(m for m in hist if m["id"] == mid)
        print("history content:", hm["content"], "| reactions:", hm["reactions"])

        await asyncio.sleep(0.4)
        stop.set()
        await bws.close()
        await asyncio.gather(t, return_exceptions=True)

    etypes = [e["type"] for e in bob_events]
    print("---")
    print("Bob live events:", etypes)
    print("PASS edit broadcast    :", any(e["type"] == "message_edited" and e["data"]["content"] == "edited text" for e in bob_events))
    print("PASS reaction broadcast:", sum(1 for e in bob_events if e["type"] == "reaction") == 3)
    print("PASS final count == 1  :", hm["reactions"] == [{"emoji": "👍", "count": 1, "me": True}])


asyncio.run(main())
