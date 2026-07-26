# Open Relay: Developer Guide

Everything needed to build a client (desktop, mobile, CLI, bot) against an Open
Relay server. Open Relay is a self-hosted chat backend: FastAPI + PostgreSQL +
Redis, a JSON REST API for state changes, and a single WebSocket for live
events. There is no proprietary transport and no SDK required; anything that
speaks HTTP and WebSocket can be a client.

This guide is written against the reference client (`frontend/`), which is the
canonical example of every flow described here.

_Open Relay and this guide by **magikh0e**. Free software under the GNU GPL-3.0._

**Covers Open Relay v1.26.0.** This line tracks the API surface below; if the
server's `GET /api/health` reports a newer version, the guide may be behind.

---

## 1. The client–server contract

A client needs exactly one piece of configuration: the **server origin**, e.g.
`https://chat.openrelay.pl`. Everything hangs off it.

| Purpose | URL |
|---|---|
| REST API | `<origin>/api/...` |
| WebSocket | `<origin>/ws?token=<access_token>` |
| Uploaded files | `<origin>/api/uploads/<id>` |

> **Topology gotcha.** In a real deployment a reverse proxy (Caddy) serves the
> SPA and forwards `/api/*` and `/ws` to the backend, **stripping the `/api`
> prefix**. So the *public* paths are `/api/...` and `/ws`, and that is what a
> client targets. If you point a client straight at a raw `uvicorn` process
> (no proxy), the backend exposes **bare** paths (`/auth/login`, not
> `/api/auth/login`, and `/ws`) because nothing is rewriting them. Build
> against the public origin, which is what these tables assume.

### How the reference client resolves the origin

The web build is served from the same host as the API, so its origin is `""`
(same-origin). A separate client isn't, so it must be told where the server is.
The resolution logic lives in one file, [`frontend/src/config.js`](../frontend/src/config.js):

```
window.__RELAY_SERVER__          →  injected by a native shell at startup
localStorage "relay_server"      →  chosen in an in-app server picker
import.meta.env.VITE_API_BASE    →  baked in at build time
""                               →  same origin (the web deployment)
```

It exposes `API_BASE` (`<origin>/api`), `wsBase()` (`ws(s)://<origin>`), and
`resolveUrl()` (absolutizes server-relative links like attachment URLs). A
native client sets the origin once and every request follows. Tokens and
encryption keys are **per-server**, so switching servers means reloading state.

---

## 2. Authentication

Auth is stateless JWT. There are two tokens:

| Token | Lifetime | Use |
|---|---|---|
| **Access** | 30 min | Sent on every request as `Authorization: Bearer <token>` |
| **Refresh** | 30 days | Exchanged for a new access token when it expires |

- Algorithm: **HS256**, signed with the server's `JWT_SECRET`. Clients never
  verify the signature; they treat tokens as opaque.
- Claims: `sub` (user id, UUID string), `type` (`"access"` or `"refresh"`;
  each is rejected where the other is expected), `tv` (token-version integer),
  `iat`, `exp`. There is no `iss`/`aud`.
- **Revocation.** Changing your password increments the user's token-version
  server-side; any token whose `tv` no longer matches is rejected on its next
  use. That instantly kills **every** outstanding access *and* refresh token,
  the mechanism behind "log out everywhere". There is no `/auth/logout`
  endpoint: ordinary logout is just discarding the tokens client-side.
- Store both tokens in the platform's secure storage (the web client uses
  `localStorage`; a desktop client should prefer the OS keychain).

### Bot tokens

A **bot account** authenticates with a long-lived opaque token instead of a JWT.
A program is meant to stay connected for weeks, and a 30-minute token with a
refresh dance is the wrong shape for that.

- Sent the same way: `Authorization: Bearer <token>`. The server tries the JWT
  path first and falls back to a bot-token lookup, so a client library needs no
  special casing.
- **No expiry and no refresh.** Revocation is deleting or rotating the token.
- Issued once, at creation or rotation, and never retrievable again: only its
  SHA-256 digest is stored.
- Carries **scopes** (`read`, `write`, `react`). A bot is refused anything
  outside them.
- A bot is **refused outright** on endpoints that only make sense for a person:
  password change, account export, account deletion, publishing encryption
  keys, and minting other bots. It has no password hash, so the login and OAuth
  paths cannot admit it either.

A bot may reach **only** the endpoints below, and only with the scope named.
Anything else is refused with `403`, including endpoints a future version adds:
the list is an allowlist, so a new route is closed to bots until it is opened
deliberately.

| Scope | Endpoints |
|---|---|
| *(none)* | `GET /users/me`, so even a write-only bot can learn its own id |
| `read` | `GET /channels`, `GET /channels/{id}`, `GET /channels/{id}/members`, `GET /channels/{id}/messages`, `GET /channels/{id}/messages/{root}/thread`, `GET /users/{id}`, `POST /channels/{id}/read`, and the WebSocket |
| `write` | `POST /channels/{id}/messages`, and `PATCH`/`DELETE` on its own messages |
| `react` | `POST /channels/{id}/messages/{id}/reactions` |

Notably absent, and deliberately: creating channels, joining or leaving on its
own initiative, opening DMs, uploading files, searching for people, and every
moderation action. A bot is put into a channel by a person and can act only
inside it.

The WebSocket accepts a bot token in the same `?token=` slot and requires
`read`. That is the difference between a bot and an incoming webhook: a webhook
can only speak, a bot can listen.

A bot has **no encryption keypair**, since there is no passphrase for a program
to hold. DMs with one are plaintext, and a group containing one cannot be
encrypted, because publishing a group key requires every member to have a
published public key (§4).

Bots reach only the channels they have been added to, through ordinary
membership. `is_bot` is exposed on every user shape so a client can label them:
a bot in a channel can read that channel, and that should be visible in the
roster rather than something people have to be told.

### The refresh flow

On any `401`, POST the refresh token to the refresh endpoint to get a fresh
pair, then retry the original request once. If the refresh itself fails, the
session is dead; clear tokens and return to sign-in. The reference
implementation is [`refreshAccess()` in `api.js`](../frontend/src/api.js).

**Before opening the WebSocket, refresh first**; the socket is long-lived but
the token in its handshake is not, so start it with a fresh access token.

> _Exact auth endpoint paths and request/response bodies: see §7._

---

## 3. The WebSocket

One socket per session carries all live events. It is **read-mostly**: clients
receive events; almost all *actions* go through REST.

**Connect:** `wss://<origin>/ws?token=<access_token>`; the access token rides
in the query string (WebSocket handshakes can't set an `Authorization` header).
A failed token closes the socket with code **1008**. On success the server
subscribes you to every channel you're a member of plus your personal topic, and
sends a first frame: `{"type":"ready","data":{"user_id":"<id>"}}`.

**Keepalive:** send `{"type":"ping"}` every **25 seconds**; the server replies
`{"type":"pong"}`. Presence is a lease with a **75 s** TTL that each ping renews
(so ~2 missed pings tolerated); stop pinging and you drop offline.

**Reconnect:** on close, reconnect with exponential backoff (the reference
client caps at 15 s), and refresh the access token before each attempt.

**Client → server frames** (all others are ignored):

| Frame | Effect |
|---|---|
| `{"type":"ping"}` | Renew presence; get a `pong` |
| `{"type":"typing","channel_id":"<id>"}` | Broadcast a typing indicator (suppressed if you disabled `share_typing`) |
| `{"type":"subscribe","channel_id":"<id>"}` | Start receiving a room's events (after joining over REST) |
| `{"type":"unsubscribe","channel_id":"<id>"}` | Stop receiving them |

**Receiving:** every frame is `{"type": <string>, "data": {...}}`. Route on
`type` (reference router: `ChatShell.onEvent`). Note that **you post messages
over REST, not the socket**; your own message echoes back as a `message` event
via the server's Redis bridge, the same way everyone else's arrives. Broadcasts
that concern a specific person (`presence`, `reaction`, `away`) carry a
`user_id` so each client can reconcile its own state.

> _Full catalogue of server→client event `type` values and payloads: see §6._

---

## 4. End-to-end encryption (direct messages and groups)

E2EE is optional and **client-side**; the server only ever stores opaque
blobs. A non-JS client can interoperate by reproducing this scheme exactly. The
reference implementation is [`frontend/src/e2ee.js`](../frontend/src/e2ee.js);
it depends only on standard primitives (WebCrypto), so any crypto library with
ECDH P-256, AES-GCM, and PBKDF2 can match it.

### Keys

- Each user holds one **ECDH P-256** keypair.
- **Public key**: exported as **SPKI**, base64-encoded, published to the server.
- **Private key**: exported as **PKCS#8**, then wrapped (see below) and stored
  server-side as an opaque blob. The server cannot unwrap it.

P-256 is used (not X25519) purely because WebCrypto supports it everywhere.

### Wrapping the private key (passphrase recovery)

The private key never leaves the device unwrapped. It is encrypted with a key
derived from the user's passphrase:

- **KDF:** PBKDF2-HMAC-SHA-256, **300,000 iterations**, random **16-byte salt**.
- **Cipher:** AES-256-GCM, random **12-byte IV**.
- Stored blob is `{ wrapped_private_key, salt, iv }`, all base64.

Forget the passphrase → the private key is unrecoverable → those messages are
gone for everyone, permanently. There is no server-side reset by design.

### Encrypting a 1:1 message

For a 1:1 DM both parties derive the **same** AES key via ECDH (A's private +
B's public ≡ B's private + A's public), so one ciphertext serves both, no
per-recipient copies.

1. `sharedKey = ECDH(myPrivate, theirPublic)` → AES-256-GCM key.
2. Encrypt with a random 12-byte IV.
3. Wire format: **`base64(iv ‖ ciphertext)`**, the 12-byte IV prepended to the
   GCM ciphertext (which includes its auth tag), all base64. This exact string
   is what you PUT as the message body.

Decryption reverses it: base64-decode, split off the first 12 bytes as the IV,
AES-GCM-decrypt the rest.

### Encrypting a group message

A group has no single pair of keys to derive from, so it uses one symmetric key
per **epoch**, sealed separately to each member under the pairwise ECDH secret
you already share with them. The server stores only sealed blobs.

**Publishing an epoch** (the group owner does this):

1. Generate a random **AES-256-GCM** key. This is the group key.
2. Export it as **raw** 32 bytes.
3. For each member, including yourself: derive `pairwise = ECDH(myPrivate,
   theirPublic)` and encrypt those raw bytes with a random 12-byte IV, giving
   `wrapped_key = base64(iv ‖ ciphertext)`.
4. `POST /dms/{id}/keys` with one share per member, each carrying `user_id`,
   `wrapped_key`, and `sender_public_key` (your own public key, so the
   recipient knows which peer to derive against).

> Wrap the **raw key bytes**, not text. A 32-byte key is generally not valid
> UTF-8, so putting it through a string-based encrypt helper will corrupt it via
> `TextDecoder` and the unwrap will produce a key that decrypts nothing.

The shares must cover **exactly** the current membership, and every member must
already have published a public key, or the request is rejected with `400`. That
rule is also what stops a bot joining an encrypted group: a bot holds no
keypair, so no share can be sealed to it.

**The server assigns the epoch number**, incrementing from the last one, rather
than trusting the client. Other members get a `group_key` WebSocket event naming
the new epoch, and fetch their share with `GET /dms/{id}/keys`.

**Sending:** encrypt exactly as for a 1:1 DM, but under the group key, and set
`key_epoch` to the current epoch on the message. The server refuses an encrypted
group message that omits it (`400`), names a superseded epoch (`409`, re-fetch
and resend), or names an epoch whose shares no longer match the membership
(`409`, the owner needs to rotate first).

**Reading:** every message carries the `key_epoch` that sealed it, so hold the
epochs you have fetched and decrypt each message with the matching one.

**Rotation** is what makes membership changes mean anything: publish a fresh
epoch whenever somebody joins or leaves. A new member receives shares only from
the epoch they were present for onward, so they cannot read earlier history; a
removed member keeps whatever they already had but gets no share of the new
epoch, so they cannot read on. Neither property survives if you skip the
rotation, which is why the server refuses to accept messages under a key whose
shares no longer match the member list.

### Encrypted attachments

Same envelope over the raw file bytes, under the pairwise key in a 1:1 DM or the
current group key in a group. The filename and MIME type are encrypted
**separately** (as their own `base64(iv‖ct)` blob, `enc_meta`) so the server
stores no hint of what the file is, just an anonymous ciphertext blob. Decrypt
the bytes for content and `enc_meta` for `{ name, type }`.

### Safety numbers (MITM defense)

Because the server distributes public keys, it could in principle substitute its
own. To rule that out, both sides compute a fingerprint over the two public keys
and compare it out of band:

```
sort([publicKeyA_b64, publicKeyB_b64]) → join with "|"
SHA-256 that string
take the first 24 bytes as 12 big-endian uint16s
each → (value % 100000) padded to 5 digits
group into twelve 5-digit blocks → "12345 67890 ..."
```

Sorting the two keys first means both sides get an identical value regardless of
who computes it. Match the numbers in person/over the phone and the channel is
verified.

A safety number is between **two people**, so a group has no single number to
compare. The lock on an encrypted group tells you the server cannot read it; it
is not a substitute for verifying each member pairwise.

---

## 5. Uploads

Files attach in **two steps**: upload the bytes to get an attachment id, then
send a message that references it via `upload_id`.

- **Request:** `POST /api/uploads`, `multipart/form-data` (auth required):
  - `file`: the file bytes (this is the field name).
  - `encrypted`: bool, default `false`.
  - `enc_meta`: string, default `""` (the encrypted `{name,type}` blob; see below).
- **Limits:** **10 MB** per file (over → `413`), **5 uploads/minute** (→ `429`).
  Plaintext uploads are restricted to images (`png jpg jpeg gif webp`) and common
  documents (`pdf txt csv md json zip doc(x) xls(x) ppt(x)`); anything else →
  `415`. SVG is deliberately rejected.
- **Response** is an `AttachmentOut` (see §7) whose `url` is `/api/uploads/<id>`;
  resolve it against the origin before fetching.
- **Serving:** `GET /api/uploads/<id>` returns the file (images inline, else as
  a download) with `X-Content-Type-Options: nosniff` and a long immutable cache.
  These URLs are **unauthenticated** capability links; anyone with the
  (unguessable) URL can fetch the file. Treat channel uploads as shareable.
- **Images are re-encoded client-side** before upload to strip EXIF/GPS
  metadata; if re-encoding fails, the reference client refuses the upload rather
  than send the original. GIFs and documents are sent as-is.
- **Encrypted uploads** (`encrypted:true`) skip type/extension checks (the bytes
  are ciphertext), and the server stores no real filename or MIME type, just an
  `application/octet-stream` blob. The true name/type travel in `enc_meta` as
  their own `base64(iv‖ct)` blob (§4). The client fetches the blob, decrypts,
  and builds its own object URL.

---

## 6. WebSocket event reference

Every server→client frame is `{"type": <string>, "data": {...}}`. `data`
shapes below. Datetimes are ISO 8601 strings; ids are UUID strings.

| `type` | `data` |
|---|---|
| `ready` | `{user_id}`, first frame after connect |
| `pong` | *(none)*, reply to your `ping` |
| `presence` | `{user_id, online}` |
| `typing` | `{channel_id, user_id}` |
| `away` | `{user_id, away, message}` |
| `message` | a full **MessageOut** (§7), new message in a room you're subscribed to |
| `message_edited` | `{id, channel_id, content, edited_at, mentions:[{id,username,display_name}]}` |
| `message_deleted` | `{id}` |
| `reaction` | `{message_id, channel_id, emoji, count, user_id, added}` |
| `channel_updated` | `{channel_id, name, topic, kind, has_password}` |
| `channel_deleted` | `{channel_id}` |
| `channel_added` | `{channel_id}`, you were added to a channel (personal topic) |
| `channel_kicked` | `{channel_id, banned}`, you were kicked/banned (personal topic) |
| `member_removed` | `{channel_id, user_id}` |
| `member_updated` | `{channel_id, user_id, role}` |
| `dm_opened` | `{channel_id}`, a DM with you was opened (personal topic) |
| `keys_published` | `{channel_id, user_id}`, a DM peer (re)published their E2EE key |
| `group_key` | `{channel_id, epoch}`, a new group key epoch was published; fetch your share with `GET /dms/{id}/keys` (§4) |

Events reach you over two topic kinds: **room** events for channels you're
subscribed to, and **personal** events (`channel_added`, `channel_kicked`,
`dm_opened`) addressed to you directly.

---

## 7. REST endpoint reference

All paths are the **public** paths; prepend the origin and `/api` (§1). Bodies
are JSON except uploads (multipart). Auth (`Authorization: Bearer <access>`) is
required on everything except `/auth/*`, `/health`, and `GET /uploads/<id>`.

### Core object shapes

Timestamps are **ISO 8601 strings** (not epoch-ms); ids are UUID strings.

**MessageOut**: canonical message, returned by history and carried by the
`message` WS event:
```
id, channel_id, sender_id (str|null),
content,                         # base64 ciphertext when encrypted==true, relay byte-for-byte
created_at, edited_at (null|ISO),
sender: {id, username, display_name, avatar_url, is_admin} | null,
reactions: [{emoji, count, me}],
reply_to: {id, sender_name, content, encrypted} | null,   # content is full ciphertext if encrypted, else truncated 140 chars
mentions: [{id, username, display_name}],
thread_root_id (str|null), reply_count, last_reply_at (null|ISO),
encrypted (bool),
key_epoch (int|null),            # group key epoch that sealed this; null for 1:1 DMs
attachment: AttachmentOut | null
```
`content` may be empty when an attachment carries the payload.

**ChannelOut**:
```
id, kind ("public"|"private"|"dm"|"group"), slug (str|null; null for DMs/groups),
name, topic, created_by (str|null), created_at, read_only (bool),
has_password (bool; true if a channel key is set, the hash is never sent),
member_count (int|null), is_member (bool|null),
unread_count (int), mention_count (int)
```
For a 1:1 DM, `name` is the other person's display name and `topic` their
username. For a group (`kind: "group"`), `name` is the group's own name and
`member_count` its real size; groups are plaintext, so E2EE never applies.

**AttachmentOut**: `{id, name, content_type, size, is_image, url, encrypted, enc_meta}`.
**UserPublic**: `{id, username, display_name, avatar_url, is_admin, is_bot}`.
**ProfileOut**: `{id, username, display_name, bio, pronouns, is_admin, created_at, registered_via_invite, invited_by_username, last_active_at}` (no email). `invited_by_username` names the admin whose invite the account redeemed (null if none/deleted); `last_active_at` is null when the target hid it (unless you're them or an admin).
**UserOut** (self): adds `has_password, share_typing, share_presence, allow_dms, discoverable, share_last_active`.
Email is exposed **only** via `GET /users/me/export`.

### Auth: `/auth`

| Method · Path | Body | Returns |
|---|---|---|
| `POST /auth/register` | `{username(3–32), email, password(8–128), display_name?}` | `201` TokenPair; auto-joins `#whatsnew`. 409 on dup, 429 (5/hr/IP) |
| `POST /auth/login` | `{username_or_email, password}` | TokenPair. 401 bad creds, 403 disabled, 429 |
| `POST /auth/refresh` | `{refresh_token}` | a **new** TokenPair. 401 if invalid/`tv` mismatch |
| `GET /auth/oauth/providers` | - | `["discord", ...]` (enabled only) |
| `GET /auth/oauth/{provider}/start` | - | `302` to provider |
| `GET /auth/oauth/{provider}/callback` | `?code&state` | `302` to `PUBLIC_BASE_URL/#access=…&refresh=…` (tokens in the URL fragment) |

`TokenPair` = `{access_token, refresh_token, token_type:"bearer"}`.

### Users: `/users`

| Method · Path | Body / Query | Returns |
|---|---|---|
| `GET /users/me` | - | UserOut |
| `PATCH /users/me` | `{display_name?, bio?≤500, pronouns?≤40}` | ProfileOut |
| `GET /users/me/settings` · `PATCH /users/me/settings` | `{share_typing?, share_presence?, allow_dms?, discoverable?, share_last_active?}` | PrivacySettings |
| `POST /users/me/password` | `{current_password?, new_password(8–128)}` | **new** TokenPair (bumps `tv`). 429 (5/5min) |
| `GET /users/search` | `?q=` (≥2) | `[UserPublic]` (≤20; respects `discoverable`) |
| `GET /users/online` | - | `[user_id]` |
| `GET /users/away` | - | `{user_id: message}` |
| `POST /users/away` | `{message?}` (empty clears) | `204` |
| `GET /users/{user_id}` | - | ProfileOut |
| `GET /users/me/export` | - | full JSON dump (incl. email + messages) |
| `POST /users/me/delete` | `{password?}` | `204`, permanent |

### Channels: `/channels`

| Method · Path | Body | Returns |
|---|---|---|
| `GET /channels` | - | `[ChannelOut]` (public directory + your privates; **DMs excluded**) |
| `POST /channels` | `{slug(2–48,^[a-z0-9-]+$), name(1–64), topic?≤512, is_private, password?(8–128)}` | `201` ChannelOut (non-admins capped at 2). 409 dup slug. `password` sets a channel key on public channels; ignored when `is_private` |
| `GET /channels/{id}` | - | ChannelOut (403 if private/DM & not a member) |
| `POST /channels/{id}/join` | `{password?}` | ChannelOut (public only). 403 if banned, or if the channel is key-protected and the password is missing/wrong. Members, invitees, owner and admins skip the key |
| `POST /channels/{id}/leave` | - | `204` |
| `GET /channels/{id}/members` | - | `[{id, username, display_name, avatar_url, is_admin, role}]` |
| `PATCH /channels/{id}` | `{name?, topic?, is_private?, password?}` | ChannelOut (owner/admin). `password`: omit = unchanged, `""`/null = remove, non-empty(≥8) = set (public only, else 400); going private clears the key |
| `DELETE /channels/{id}` | - | `204` (owner/admin) |
| `POST /channels/{id}/read` | - | `204`, mark read up to now |
| `POST /channels/{id}/{kick\|ban\|unban\|invite}` | `{user_id, reason?}` | `204` |
| `POST /channels/{id}/role` | `{user_id, role}` | `204` |

### Messages: `/channels/{channel_id}/messages`

| Method · Path | Body / Query | Returns |
|---|---|---|
| `GET ""` | `?before=<ISO>` (older than), `?around=<msg_id>` (jump window), `?limit=` (default 50, ≤100) | `[MessageOut]` **chronological**, top-level only. Paginate by passing the oldest loaded `created_at` as `before` |
| `GET "/{root_id}/thread"` | - | `[MessageOut]` root + replies |
| `POST ""` | `{content(≤12000), reply_to_id?, thread_root_id?, upload_id?, encrypted}` | `201` MessageOut. `encrypted:true` **DMs only** (else 400). 429 (10/10s) |
| `PATCH "/{id}"` | `{content(1–4000)}` | MessageOut (own only; not encrypted msgs) |
| `DELETE "/{id}"` | - | `204` (own or admin; soft delete) |
| `POST "/{id}/reactions"` | `{emoji(1–32)}` | `{emoji, count, me}`, **toggles** |

### DMs: `/dms`

| Method · Path | Body | Returns |
|---|---|---|
| `GET /dms` | - | `[ChannelOut]` your DMs and groups (`kind` `"dm"` or `"group"`) |
| `POST /dms` | `{user_id}` | `201` ChannelOut (one per pair; 403 if peer disallows DMs) |
| `DELETE /dms/{id}` | - | `204`, hides it from your sidebar only (1:1 DMs) |
| `POST /dms/group` | `{user_ids[2..19], name?}` | `201` ChannelOut (`kind: "group"`). Creator is added automatically; max 20 people total. 403 if any invitee disallows DMs |
| `POST /dms/{id}/members` | `{user_id}` | `204`, owner-only; idempotent; 400 if full. Leave a group via `POST /channels/{id}/leave` |
| `DELETE /dms/{id}/members/{user_id}` | - | `204`, owner-only; removes a member |
| `POST /dms/{id}/keys` | `{shares: [{user_id, wrapped_key, sender_public_key}]}` | `201` GroupKeysOut. Owner only. Publishes the next key epoch (number assigned by the server). Shares must cover **exactly** the current members and every one of them must have published a public key, else `400` |
| `GET /dms/{id}/keys` | - | `{keys: [{epoch, wrapped_key, sender_public_key}], current_epoch: int\|null}`: every epoch sealed to you, oldest first. `current_epoch` is null for a plaintext group |

### Bots: `/bots` (admin only)

| Method · Path | Body | Returns |
|---|---|---|
| `GET /bots` | - | `[BotOut]` |
| `POST /bots` | `{username, display_name?, scopes[]}` | `201` BotOut **plus `token`**, shown once. 409 on a taken username, 400 on an unknown scope |
| `POST /bots/{id}/token` | - | BotOut plus a new `token`; the previous one stops working immediately. Identity, scopes and channel memberships are kept |
| `DELETE /bots/{id}` | - | `204`. Messages survive with the sender nulled, as for a deleted person |

**BotOut**: `{id, username, display_name, scopes[], created_at, last_used_at}`.

Add a bot to a channel the same way as anybody else, with
`POST /channels/{id}/invite`.

### Keys: `/keys` (see §4)

| Method · Path | Body | Returns |
|---|---|---|
| `GET /keys/me` | - | `{public_key, wrapped_private_key, salt, iv}` (404 if none) |
| `PUT /keys/me` | same four fields | echoes bundle; broadcasts `keys_published` |
| `GET /keys/{user_id}` | - | `{user_id, public_key}` (404 if peer has no keys) |

### Push: `/push`

| Method · Path | Body | Returns |
|---|---|---|
| `GET /push/key` | - | `{public_key}` (VAPID) |
| `POST /push/subscribe` · `POST /push/unsubscribe` | `{endpoint, p256dh, auth}` | `204` |

### Search / Giphy / Moderation

| Method · Path | Query | Returns |
|---|---|---|
| `GET /search` | `?q=` (≥2), `?limit=` (30, ≤100) | `[{id, channel_id, channel_name, channel_kind, sender, content, created_at, thread_root_id}]`, your channels only; **encrypted messages excluded** |
| `GET /giphy/enabled` · `/giphy/trending` · `/giphy/search` | `?q, ?limit` | `{enabled}` / `[{id, title, url, preview, width, height}]` (503 if unconfigured) |
| `POST /moderation/{ban\|unban}` | `?target_id&reason` | `204` (admin only) |
| `GET /moderation/audit` | `?limit=` (100, ≤500) | `[{id, actor, action, target, channel_id, detail, created_at}]` (admin only) |

---

## 8. Rate limits & errors

Errors follow FastAPI's convention: non-2xx responses carry a JSON body
`{"detail": "..."}` (occasionally `detail` is a structured array for validation
errors). Handle these status codes explicitly:

| Status | Meaning | Client action |
|---|---|---|
| `401` | Access token missing/expired/revoked | Refresh once, retry; else sign out |
| `403` | Authenticated but not permitted | Surface, don't retry |
| `429` | Rate limited | Back off |

Default limits (server-configurable):

| Action | Limit |
|---|---|
| Messages | 10 per 10 s |
| Login attempts | 10 per identifier per minute |
| Registration | 5 per hour per IP |
| Uploads | 5 per minute |

---

## 9. Minimal client, end to end

The smallest useful client, in pseudo-code:

```
origin = "https://your-server"

# 1. Authenticate
{access, refresh} = POST origin/api/auth/login {username_or_email, password}

# 2. Open the live socket (refresh access first)
ws = connect  wss://origin/ws?token=access
every 25s: ws.send {type:"ping"}
ws.onmessage: dispatch on frame.type   # see §6

# 3. Load state over REST (Bearer access on every call)
channels = GET origin/api/channels
history  = GET origin/api/channels/<id>/messages?<pagination>   # see §7

# 4. Act over REST; the echo arrives back over the socket
POST origin/api/channels/<id>/messages {content}

# 5. On any 401: refresh once and retry
{access, refresh} = POST origin/api/auth/refresh {refresh_token: refresh}
```

Everything else (reactions, edits, DMs, presence, typing, encryption) is a
variation on this loop: **change state with a REST call, learn about changes
(yours and everyone else's) from the socket.**

---

## 10. Building a native/desktop client

- **Point it at the origin.** Set `window.__RELAY_SERVER__` (or the equivalent)
  before the app boots; §1 does the rest.
- **CORS.** A browser-based webview (Tauri, Electron) enforces CORS, so add the
  shell's origin (e.g. `tauri://localhost`, `app://.`, or an Electron dev
  origin) to the server's `CORS_ORIGINS`. A pure-native HTTP client isn't subject
  to CORS. WebSocket connections are not CORS-gated.
- **Token storage.** Use the OS keychain, not a flat file.
- **OAuth.** The web SSO flow is a full-page browser redirect back to the app
  origin; a native client needs a system-browser + custom-scheme redirect
  (or a username/password login), which the server's redirect handling must be
  configured for. Plan for password login first.
- **Re-implement the browser-only bits:** image re-encode/EXIF strip (§5),
  E2EE via a native crypto lib (§4), and push via the platform service (APNs/
  FCM) rather than Web Push.

---

## 11. Webhooks

Incoming webhooks let an external system post messages into a channel over a
secret URL, with no user account. Handy for piping CI results, alerts, or
home-automation events into a channel.

### Create and manage

Authenticated, and gated to the channel's owner/mod (or a site admin):

| Method · Path | Body | Returns |
|---|---|---|
| `POST /channels/{id}/webhooks` | `{name}` | `201` with a one-time `url` that embeds the secret token. Store it; it is not shown again |
| `GET /channels/{id}/webhooks` | | `[{id, channel_id, name, created_at}]` (no tokens) |
| `DELETE /channels/{id}/webhooks/{webhook_id}` | | `204`, revokes it |

Create response:
```
{ "id": "...", "channel_id": "...", "name": "CI",
  "created_at": "2026-...", "url": "https://<origin>/api/webhooks/<id>/<token>" }
```

### Fire it

Anything that can POST JSON posts a message. No auth header; the secret is the
URL itself:

```bash
curl -X POST https://<origin>/api/webhooks/<id>/<token> \
  -H 'content-type: application/json' \
  -d '{"text": "build #482 passed", "name": "CI"}'
```

- `text` (required): the message body, up to 4000 chars. `@mentions` resolve.
- `name` (optional): overrides the webhook's display name for this post.

The message appears from that display name with a null user sender (MessageOut
carries `author_name`, §7). Rate limited to **60 posts per minute** per webhook;
a missing or wrong token returns a uniform `404`.
