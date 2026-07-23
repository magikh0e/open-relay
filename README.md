# Chat Service

A self-hosted, IRC-style chat service with public & private channels, DMs,
real-time messaging, presence, and typing indicators.

- **Backend:** FastAPI + WebSockets, PostgreSQL, Redis pub/sub
- **Frontend:** React + Vite
- **Auth:** JWT access/refresh, argon2 password hashing

## Architecture at a glance

```
Browser (React)
   │  HTTP (REST)                 WebSocket (one per session)
   ▼                              ▼
FastAPI workers  ── persist ──▶  PostgreSQL
   │  publish/subscribe
   ▼
 Redis  ◀── fan-out ──▶  every worker forwards to its local sockets
```

- Public channels, private channels, and DMs are all rows in one `channels`
  table (distinguished by `kind`). Every message flows through one `messages`
  table, and every live subscription is just "a channel" — which keeps the
  real-time layer uniform.
- A chat message is **persisted to Postgres and published to Redis**. Redis
  fans it out to all worker processes, so you can run many workers / hosts with
  no sticky sessions.

## Run it (development)

**1. Start Postgres + Redis** (Docker):

```bash
docker compose up -d
```

**2. Backend:**

```bash
cd backend
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then set a real JWT_SECRET
alembic upgrade head          # create the schema
uvicorn app.main:app --reload --port 8000
```

The API is now at http://localhost:8000 (docs at `/docs`).

> The schema is managed by **Alembic**. Run `alembic upgrade head` after
> pulling changes that add migrations. For a quick throwaway dev DB you can skip
> Alembic and set `AUTO_CREATE_TABLES=1` in `.env` to have tables created on
> startup instead.

**3. Frontend:**

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` and `/ws` to the backend, so
everything is same-origin in dev.

## Try it

1. Register two accounts (open a second browser / incognito window).
2. Create a channel, send messages — they appear live in both windows.
3. Search for the other user under **Direct Messages → +** to open a DM.
4. Watch presence dots and the "typing…" indicator update in real time.

## Making yourself an admin (for moderation)

Site admins can ban/unban users via `/moderation/*`. Flip the flag directly in
the DB for your first admin:

```sql
UPDATE users SET is_admin = true WHERE username = 'you';
```

## Security notes

- **XSS:** the primary defense is output encoding — the React frontend renders
  all user content as text nodes and **never** uses `innerHTML` /
  `dangerouslySetInnerHTML`. `MessageContent.jsx` parses @mentions by building
  React elements (not HTML strings), so a message like `<script>…` renders as
  literal text. Verified: an `alert()` payload produces no `<script>` node and
  is entity-escaped in the DOM.
- **Input hardening (defense-in-depth):** `app/sanitize.py` normalizes Unicode
  (NFC) and strips control/format/zero-width/bidi-override characters, then
  hard-caps length, on all free-text (messages, display name, bio, pronouns).
- **Avatars are initials only** — no user-supplied image URLs, so there's no
  SSRF / tracking-pixel / `src`-injection surface.
- **Mentions** are resolved server-side against real, active users;
  usernames follow a strict `[a-zA-Z0-9_.-]{3,32}` charset and email-like
  `foo@bar` text is never treated as a mention.
- **Admin (`is_admin`)** can only be granted via direct DB access (no API
  writes it); it's never carried in the JWT (read fresh from the DB per
  request) and every privileged action is enforced server-side — the UI crown
  is cosmetic.
- **Login throttling:** Redis-backed limit of `LOGIN_RATE_PER_MIN` attempts per
  identifier per minute (plus a looser per-IP cap) → 429 on brute force.
- **Audit log:** all moderation actions (site & channel ban/kick/unban) are
  recorded to `audit_logs` with actor, target, and timestamp; readable by
  admins at `GET /moderation/audit`.

## Deployment

See **[DEPLOY.md](DEPLOY.md)** for the full containerized VPS deployment
(Caddy + automatic HTTPS, gunicorn/uvicorn, Postgres, Redis) and the
push-to-deploy GitHub Actions workflow. Migrations run automatically on
container startup.

## Roadmap (post-MVP)

- [x] Alembic migrations, CI, Docker images, VPS deploy (see DEPLOY.md)
- [x] Message editing + emoji reactions
- [x] Inline replies (reply-to a message with quoted preview)
- [x] @mentions (autocomplete, highlight, mention-me) + user profiles (bio/pronouns)
- [x] Channel moderation: kick / ban / unban + admin crowns + owner/mod roles
- [x] Delete channels (site admin or channel owner) — cascades + audited
- [x] Channel operators (IRC-style op = mod role): owner/admin grant/revoke, editable topic
- [x] Channel settings panel: name/topic/privacy, role management, ownership transfer, delete
- [ ] Role management UI (promote to mod, transfer ownership)
- [x] Threads (Slack-style: root + replies in a side panel, flattened, live)
- [ ] File & image uploads
- [ ] Full-text message search
- [ ] Read receipts / unread badges (`last_read_at` column already exists)
- [ ] Rate-limit tuning + abuse reporting
- [ ] Refresh-token rotation/revocation (currently stateless)
