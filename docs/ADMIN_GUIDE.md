# Server admin guide

Running your own Open Relay instance: configuration, admins, invites, secret
rotation, and day-2 operations. For the initial VPS deploy see
[DEPLOY.md](../DEPLOY.md); for backups and monitoring see
[ops/BACKUP.md](../ops/BACKUP.md) and [ops/UPTIME.md](../ops/UPTIME.md).

The stack is one Docker Compose project: `backend` (FastAPI), `db` (Postgres),
`redis`, and `web` (Caddy, the only publicly exposed service, which terminates
HTTPS and serves the frontend). Run every command below from the directory
holding `docker-compose.prod.yml` and `.env.prod`.

## Configuration (`.env.prod`)

The backend reads all of these from `.env.prod` at startup. After editing, apply
with `docker compose -f docker-compose.prod.yml up -d backend` (use `up -d`, not
`restart`, so the new values are read).

| Variable | What it does |
|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | Database credentials; `DATABASE_URL` is built from them. See "Rotating secrets" before changing the password. |
| `JWT_SECRET` | Signs auth tokens. Rotating it logs everyone out (see below). Set a long random value. |
| `PUBLIC_BASE_URL` | The canonical public origin (e.g. `https://chat.example.com`). Used to build OAuth redirect URIs and the post-login landing. Must match a registered OAuth callback. |
| `CORS_ORIGINS` | Comma-separated browser origins allowed to call the API. Include your web origin, and `tauri://localhost`, `http://tauri.localhost` for the desktop app. |
| `REGISTRATION_MODE` | `open` (anyone can sign up) or `invite` (a code is required). See "Registration and invites". |
| `SITE_ADDRESS` | Caddy: the domain(s) to serve with automatic HTTPS (space-separated for several). `:80` for plain HTTP/IP testing. |
| `REDIRECT_FROM` | Caddy: an old domain to 301-redirect to the canonical site. |
| `GOOGLE_CLIENT_ID` / `_SECRET`, `DISCORD_CLIENT_ID` / `_SECRET` | OAuth credentials; leave blank to disable that provider. |
| `VAPID_PRIVATE_KEY` / `VAPID_PUBLIC_KEY` | Web-push signing keypair. Set your own so push subscriptions survive restarts. |
| `GIPHY_API_KEY` | Enables the GIF picker; blank disables it. |
| `MAX_UPLOAD_MB` | Per-file upload cap (default 10). |
| `LOGIN_RATE_PER_MIN` / `REGISTER_RATE_PER_HOUR` / `UPLOAD_RATE_PER_MIN` | Abuse throttles (defaults 10 / 5 / 5). |
| `ACCESS_TOKEN_TTL_MIN` / `REFRESH_TOKEN_TTL_DAYS` | Session lifetimes (defaults 30 min / 30 days). |
| `PURGE_AFTER_DAYS` | Deleted messages and orphaned files are hard-purged after this many days (default 30). |
| `GUNICORN_WORKERS` | Backend worker processes (default 2). They are async, so one serves many concurrent requests; the sync-era `(2 x cores) + 1` rule over-provisions and mainly costs memory. Raise it on a larger box. |

> The database schema is migrated automatically on backend start
> (`alembic upgrade head` in the entrypoint), so a deploy never needs a manual
> migration step.

## Admins and moderation

There is no in-app "make admin" button by design. Grant it directly in the
database:

```bash
docker compose -f docker-compose.prod.yml exec db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "UPDATE users SET is_admin = true WHERE username = 'YOUR_USERNAME';"
```

A site admin can moderate any channel (kick, ban, delete, change roles), reach
anyone regardless of DM settings, mint invite codes, and always bypasses a
channel password. Per-channel moderation (op/kick/ban/topic) is handled by that
channel's owner and operators; admin is the site-wide escalation.

## Registration and invites

- **Open** (`REGISTRATION_MODE=open`): anyone can sign up. The register endpoint
  is rate-limited (`REGISTER_RATE_PER_HOUR`).
- **Invite-only** (`REGISTRATION_MODE=invite`): sign-up requires a single-use
  code. Fits the "people you actually know" model and shuts off signup abuse.

Mint and manage codes as an admin from **your profile: Invites** (generate,
copy, revoke; you can see who created each code and who used it). The same
actions are available via the API: `POST /api/invites`, `GET /api/invites`,
`DELETE /api/invites/{id}`.

## Domains, CORS, and OAuth

- Point DNS at the VPS and set `SITE_ADDRESS` to the domain; Caddy gets a
  Let's Encrypt certificate automatically.
- `CORS_ORIGINS` must list every origin the browser (or desktop app) calls from,
  or requests fail with "Failed to fetch".
- OAuth uses one `PUBLIC_BASE_URL`. Sign-in always redirects there, so if you
  serve the app on several hostnames, OAuth users land on `PUBLIC_BASE_URL`
  regardless of where they started. Register
  `PUBLIC_BASE_URL/api/auth/oauth/<provider>/callback` in the provider's
  developer portal, or sign-in breaks.

## Backups and monitoring

- **Backups**: encrypted Postgres + uploads archive, offsite copy, retention,
  and a tested restore. See [ops/BACKUP.md](../ops/BACKUP.md). Do this first; the
  E2EE keys make some data unrecoverable if the box is lost without a backup.
- **Uptime**: point a monitor at `/api/health` from somewhere other than the box
  it watches, so an outage can actually be reported. A hosted checker is the
  simplest option. See [ops/UPTIME.md](../ops/UPTIME.md).

## Disk: prune the Docker build cache

Push-to-deploy rebuilds both images on every deploy, and Docker never reclaims
the build cache by itself. On an actively developed server it reached **3GB in
two days**, which is enough to matter on a small VPS. Check it with:

```bash
docker system df          # look at the Build Cache row
docker builder prune -f --filter until=168h    # release anything over a week old
```

This only discards cached build layers. Images, containers and volumes are
untouched, and the next deploy simply rebuilds what it needs.

To automate it, `ops/systemd/openrelay-prune.{service,timer}` runs that weekly.
Installing them needs no root if your user can run `docker`:

```bash
mkdir -p ~/.config/systemd/user
cp ops/systemd/openrelay-prune.* ~/.config/systemd/user/
loginctl enable-linger "$USER"     # so it runs when you are not logged in
systemctl --user daemon-reload
systemctl --user enable --now openrelay-prune.timer
systemctl --user list-timers openrelay-prune.timer
```

Drop them in `/etc/systemd/system/` and use `systemctl` without `--user` if you
would rather run it as root.

## Rotating secrets

Where a value lives decides how you change it. Anything read at startup (JWT
secret, OAuth creds, `PUBLIC_BASE_URL`) is an `.env.prod` edit + `up -d backend`.
Anything persisted in a volume (the Postgres password) is changed inside the
running service first, then in the env.

**JWT secret.** Signs every token, so rotating it logs everyone out (no data
loss; also your "log everyone out everywhere" lever):

```bash
sed -i "s#^JWT_SECRET=.*#JWT_SECRET=$(openssl rand -base64 48)#" .env.prod
docker compose -f docker-compose.prod.yml up -d backend
```

**Postgres password.** The catch: `POSTGRES_PASSWORD` only takes effect when
Postgres first initialises an empty data dir. Your data already lives in the
`pgdata` volume, so the real password is stored there, not in the env. Change it
in the database first, then update the env, or the backend can't reconnect:

```bash
# 1) change the live password
docker compose -f docker-compose.prod.yml exec db \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  -c "ALTER USER \"$POSTGRES_USER\" WITH PASSWORD 'NEW_PASSWORD';"
# 2) set POSTGRES_PASSWORD=NEW_PASSWORD in .env.prod
# 3) recreate the backend so DATABASE_URL picks it up
docker compose -f docker-compose.prod.yml up -d backend
```

The `db` container needn't be recreated; the `ALTER` already changed the live
password. Backups are unaffected (they reach the DB over the local socket via
`docker compose exec`, never the password).

## Deploying updates

If you use the push-to-deploy hook (see DEPLOY.md), `git push vps main` rebuilds
and restarts, running migrations automatically. Otherwise, on the VPS:

```bash
git pull
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

For a config-only change, no rebuild is needed: edit `.env.prod` and
`docker compose -f docker-compose.prod.yml up -d backend`.

## Troubleshooting

- **"Failed to fetch" / CORS errors in the browser**: the origin isn't in
  `CORS_ORIGINS`. Add it and `up -d backend`.
- **Backend won't start**: check its logs for an auth or connection error:
  `docker compose -f docker-compose.prod.yml logs --tail=50 backend`. A common
  cause is a `POSTGRES_PASSWORD` changed in the env but not in the database (see
  above).
- **Everyone was logged out**: expected after a `JWT_SECRET` change.
- **OAuth loops or lands on the wrong host**: `PUBLIC_BASE_URL` and the
  provider's registered callback disagree.
- **Health check**: `curl -s https://<your-domain>/api/health` should return
  `{"status":"ok",...}`.
