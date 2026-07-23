"""End-to-end real-time test: connect two WebSocket clients and confirm live
delivery of messages, presence, and typing across the Redis bridge."""
import asyncio
import json
import urllib.request

import websockets

BASE = "http://localhost:8000"
WS = "ws://localhost:8000/ws"


def post(path, body, token=None):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def login(user):
    return post("/auth/login", {"username_or_email": user, "password": "password123"})


async def collect(ws, seen, stop):
    try:
        async for raw in ws:
            seen.append(json.loads(raw))
            if stop.is_set():
                return
    except Exception:
        pass


async def main():
    alice = login("alice")["access_token"]
    bob = login("bob")["access_token"]

    # Find the shared 'general' channel from Bob's channel list.
    def get(path, token):
        req = urllib.request.Request(BASE + path, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    channels = get("/channels", bob)
    general = next(c for c in channels if c["slug"] == "general")

    bob_seen, alice_seen = [], []
    stop = asyncio.Event()

    async with websockets.connect(f"{WS}?token={bob}") as bws, \
               websockets.connect(f"{WS}?token={alice}") as aws:
        bt = asyncio.create_task(collect(bws, bob_seen, stop))
        at = asyncio.create_task(collect(aws, alice_seen, stop))
        await asyncio.sleep(0.5)  # let both sockets register + presence fire

        # Alice types, then posts a message over HTTP.
        await aws.send(json.dumps({"type": "typing", "channel_id": general["id"]}))
        await asyncio.sleep(0.2)
        post(f"/channels/{general['id']}/messages", {"content": "live over websocket!"}, alice)

        await asyncio.sleep(0.8)
        stop.set()
        await bws.close()
        await aws.close()
        await asyncio.gather(bt, at, return_exceptions=True)

    def types(seen):
        return [m["type"] for m in seen]

    print("Bob   received:", types(bob_seen))
    print("Alice received:", types(alice_seen))

    got_msg = [m for m in bob_seen if m["type"] == "message"]
    got_typing = [m for m in bob_seen if m["type"] == "typing"]
    got_presence = [m for m in bob_seen + alice_seen if m["type"] == "presence"]

    print("---")
    print("PASS live message :", bool(got_msg) and got_msg[0]["data"]["content"] == "live over websocket!")
    print("PASS typing signal:", bool(got_typing))
    print("PASS presence     :", bool(got_presence))
    print("PASS ready frame  :", any(m["type"] == "ready" for m in bob_seen))


asyncio.run(main())
