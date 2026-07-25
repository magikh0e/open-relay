# Deploying to a VPS (for testing)

The whole app is containerized: **Caddy** (reverse proxy + static frontend +
automatic HTTPS), **backend** (gunicorn/uvicorn), **Postgres**, and **Redis**.
One `docker compose` command brings it all up. Only Caddy is exposed publicly;
the database and Redis stay on the internal Docker network.

```
                 :80 / :443
                     │
                 ┌───▼────┐   /api/*  ┌──────────┐
   Internet ────▶│  Caddy │──────────▶│ backend  │──▶ Postgres
                 │  (web) │   /ws      │ (4 wkrs) │──▶ Redis
                 └───┬────┘            └──────────┘
                     │ everything else
                     ▼
              static React SPA
```

## Prerequisites

- A VPS (Ubuntu 22.04/24.04 is easiest), 1 GB RAM is enough for testing.
- SSH access.
- **Optional but recommended:** a domain (or subdomain) with an `A` record
  pointing at the VPS IP. With a domain, Caddy issues a real HTTPS cert
  automatically. Without one, you can test over plain HTTP on the IP.

## 1. Install Docker on the VPS

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER    # then log out/in so `docker` works without sudo
```

## 2. Get the code onto the VPS

From your machine (the app lives in `C:\Users\jerem\Downloads\chat-app`):

```bash
# simplest: copy the whole folder up
scp -r C:/Users/jerem/Downloads/chat-app user@YOUR_VPS_IP:~/chat-app
```

Or push it to a git repo and `git clone` it on the VPS. The `.gitignore`
already excludes `.env.prod`, `.venv/`, and `node_modules/`.

## 3. Configure environment

On the VPS:

```bash
cd ~/chat-app
cp .env.prod.example .env.prod
nano .env.prod
```

Set these:

| Variable | Value |
|---|---|
| `POSTGRES_PASSWORD` | a strong random password |
| `JWT_SECRET` | `openssl rand -base64 64` |
| `SITE_ADDRESS` | your domain (e.g. `chat.example.com`) **or** `:80` for IP-only |
| `CORS_ORIGINS` | your public URL (e.g. `https://chat.example.com`) |
| `HTTP_PORT` / `HTTPS_PORT` | leave `80` / `443` |

> **HTTPS is automatic** when `SITE_ADDRESS` is a domain that resolves to this
> VPS; Caddy handles Let's Encrypt for you. Just make sure ports 80 and 443
> are open and the DNS `A` record is live *before* starting.

## 4. Open the firewall

```bash
sudo ufw allow 22
sudo ufw allow 80
sudo ufw allow 443
sudo ufw enable
```

## 5. Launch

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
```

First build takes a few minutes (npm build + pip install). Check status:

```bash
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs -f backend
```

Visit `https://chat.example.com` (or `http://YOUR_VPS_IP`). Register two
accounts in two browsers and watch messages, presence, and typing update live.

## 6. Make yourself an admin (moderation)

```bash
docker compose -f docker-compose.prod.yml exec db \
  psql -U chat -d chat -c "UPDATE users SET is_admin = true WHERE username='YOUR_USERNAME';"
```

## Everyday operations

```bash
# view logs
docker compose -f docker-compose.prod.yml logs -f

# restart after pulling new code
git pull   # or re-scp
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

# stop everything (keeps data)
docker compose -f docker-compose.prod.yml down

# stop AND wipe the database/redis volumes
docker compose -f docker-compose.prod.yml down -v
```

## SSO / OAuth (Google & Discord): optional

Sign-in with Google/Discord is built in and **auto-enables per provider** once
you set its credentials (buttons only show for configured providers).

**1. Register an OAuth app with each provider** and set the redirect URI to
your domain + `/api/auth/oauth/<provider>/callback`:

| Provider | Where | Redirect URI |
|---|---|---|
| Google | console.cloud.google.com → APIs & Services → Credentials → OAuth client (Web) | `https://YOUR_DOMAIN/api/auth/oauth/google/callback` |
| Discord | discord.com/developers → your app → OAuth2 → Redirects | `https://YOUR_DOMAIN/api/auth/oauth/discord/callback` |

(For local dev use `http://localhost:5173/api/auth/oauth/<provider>/callback`.)

**2. Put the credentials in `.env.prod`:**

```
PUBLIC_BASE_URL=https://YOUR_DOMAIN
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
DISCORD_CLIENT_ID=...
DISCORD_CLIENT_SECRET=...
```

`PUBLIC_BASE_URL` must match your real origin; it's used to build the redirect
URIs and to hand tokens back to the browser. Redeploy and the SSO buttons
appear on the login screen.

**How it works / safety:** on callback the backend verifies a Redis-stored CSRF
`state`, exchanges the code, and finds-or-creates a passwordless user. It only
**links to an existing account on a verified-email match** (never on an
unverified address; that would be an account-takeover vector), and tokens are
returned to the SPA via the URL fragment (not query string, so they don't hit
logs).

## Continuous deployment (push to deploy)

The repo ships `.github/workflows/deploy.yml`. On every push/PR it builds the
backend, frontend, and both Docker images. On a push to `main` it then SSHes
into your VPS and redeploys automatically.

**One-time setup so the workflow can deploy:**

1. **Put the code on the VPS as a git checkout** (instead of `scp`):

   ```bash
   cd ~
   git clone YOUR_REPO_URL chat-app
   cd chat-app
   cp .env.prod.example .env.prod   # then edit it (this file is gitignored)
   docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
   ```

2. **Create a deploy SSH key** (on your machine), add the public half to the
   VPS, and the private half to GitHub:

   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N ""
   ssh-copy-id -i deploy_key.pub user@YOUR_VPS_IP     # authorizes the key
   ```

3. **Add repo secrets** in GitHub → Settings → Secrets and variables → Actions:

   | Secret | Value |
   |---|---|
   | `VPS_HOST` | your VPS IP or hostname |
   | `VPS_USER` | the SSH user (e.g. `ubuntu`) |
   | `VPS_SSH_KEY` | the **contents** of the private `deploy_key` file |
   | `VPS_SSH_PORT` | *(optional)* SSH port if not 22 |

After that, `git push` to `main` → the VPS pulls, rebuilds, and restarts. The
migrations run automatically on backend startup, so schema changes ship with
the code. Trigger a manual redeploy anytime from the **Actions** tab
("Run workflow").

## Notes & caveats for this test deployment

- **Schema / migrations:** the backend container runs `alembic upgrade head` on
  startup (see `backend/entrypoint.sh`), so schema changes ship with the code.
  To add a migration after changing models: from `backend/` with the DB
  reachable, run `alembic revision --autogenerate -m "describe change"`, review
  the generated file in `alembic/versions/`, and commit it. If you ever run
  **multiple backend replicas**, apply migrations as a separate one-shot step
  before rolling the app rather than letting every replica race on startup.
- **Backups:** the Postgres data lives in the `pgdata` Docker volume. Back it up
  with `docker compose ... exec db pg_dump -U chat chat > backup.sql`.
- **Refresh tokens** are currently stateless (no server-side revocation). Fine
  for testing; add rotation/revocation before real production.
- **Scaling:** the backend runs 4 workers and fans messages out through Redis,
  so you can raise `-w` in `backend/Dockerfile` or run multiple backend
  replicas without any sticky-session config.
- **Resource use:** on a 1 GB VPS this runs comfortably for a handful of test
  users. Bump the droplet for real load testing.

## Verified locally

This exact stack was built and smoke-tested with `docker-compose.prod.yml`
before you got it: SPA served through Caddy, `/api/*` prefix-stripped to the
backend, user registration, and the `/ws` WebSocket upgrade all confirmed
working end-to-end in containers.
