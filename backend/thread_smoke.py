"""Test threads: main-timeline exclusion, reply counts, flattening, live WS."""
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
    cid = next(c for c in req("GET", "/channels", alice) if c["slug"] == "general")["id"]
    req("POST", f"/channels/{cid}/join", bob)

    got = []
    async with websockets.connect(f"ws://localhost:8000/ws?token={bob}") as ws:
        task = asyncio.create_task(_collect(ws, got))
        await asyncio.sleep(0.4)

        root = req("POST", f"/channels/{cid}/messages", alice, {"content": "THREAD ROOT topic"})
        r1 = req("POST", f"/channels/{cid}/messages", bob, {"content": "reply one", "thread_root_id": root["id"]})
        r2 = req("POST", f"/channels/{cid}/messages", alice, {"content": "reply two", "thread_root_id": root["id"]})
        # reply to a reply -> should flatten to the same root
        r3 = req("POST", f"/channels/{cid}/messages", bob, {"content": "reply three", "thread_root_id": r1["id"]})

        await asyncio.sleep(0.5)
        await ws.close()
        await asyncio.gather(task, return_exceptions=True)

    hist = req("GET", f"/channels/{cid}/messages", alice)
    hist_ids = {m["id"] for m in hist}
    root_in_hist = next((m for m in hist if m["id"] == root["id"]), None)

    thread = req("GET", f"/channels/{cid}/messages/{root['id']}/thread", alice)

    print("=== flattening ===")
    print("PASS reply-to-reply flattened to root:", r3["thread_root_id"] == root["id"])

    print("\n=== main timeline excludes thread replies ===")
    print("PASS root shown in channel:", root["id"] in hist_ids)
    print("PASS replies NOT in channel timeline:",
          all(x["id"] not in hist_ids for x in (r1, r2, r3)))

    print("\n=== reply count on root ===")
    print("root reply_count:", root_in_hist and root_in_hist["reply_count"])
    print("PASS root shows 3 replies:", root_in_hist and root_in_hist["reply_count"] == 3)
    print("PASS last_reply_at set:", root_in_hist and root_in_hist["last_reply_at"] is not None)

    print("\n=== thread fetch ===")
    print("thread contents:", [m["content"] for m in thread])
    print("PASS thread = root + 3 replies in order:",
          [m["content"] for m in thread] == ["THREAD ROOT topic", "reply one", "reply two", "reply three"])

    print("\n=== live delivery ===")
    thread_events = [e for e in got if e["type"] == "message" and e["data"].get("thread_root_id")]
    print("PASS bob received 3 live thread replies:", len(thread_events) == 3)


async def _collect(ws, out):
    try:
        async for raw in ws:
            out.append(json.loads(raw))
    except Exception:
        pass


asyncio.run(main())
