# Contributing to Open Relay

Thanks for wanting to help. Open Relay is a small, self-hosted chat service, and
it stays small on purpose. This document covers how to get it running, what the
house rules are, and the few things that will get a change turned down no matter
how good the code is.

By contributing you agree that your work is licensed under the
[GNU GPL-3.0](LICENSE), the same terms as the rest of the project.

---

## Reporting a security problem

**Do not open a public issue for a security vulnerability.** This is a chat app
people trust with private conversations, so a public report is a live exploit
notice.

Report it privately through GitHub instead: go to the repository's **Security**
tab and choose **Report a vulnerability**. That opens a private advisory only the
maintainer can see.

Include what you found, how to reproduce it, and what an attacker could do with
it. You will get an acknowledgement, and credit in the release notes if you want
it.

Things worth reporting: anything that leaks message content across accounts,
bypasses channel membership or the encryption boundary, escalates to admin,
allows XSS or SSRF, or defeats the rate limits.

---

## Ground rules

These are the ones that matter most. Everything else is negotiable.

### 1. Do not weaken the privacy story

The project's whole pitch is an honest account of what it does and does not
protect. A change that quietly widens the gap between the claim and the code is
worse than a bug. Specifically, the following will be rejected:

- Analytics, telemetry, crash reporting, or any "phone home" behaviour, including
  opt-in. There is currently none anywhere in the codebase, and
  [the website says so](https://openrelay.pl/).
- Anything that sends user content to a third party, or uses it as training data
  for a model.
- New third-party runtime calls from the client without a proxy. GIF search goes
  through the server precisely so the provider never sees a user's IP; hold new
  integrations to that standard.

If a feature genuinely needs one of these, open an issue first and explain the
tradeoff. Do not open with the code already written.

### 2. If you change the security boundary, say so plainly

The README, the in-app privacy policy and the marketing site all describe exactly
who can read what. If your change moves that line, even slightly, update all
three in the same pull request and call it out in the description. Examples of
changes that move the line: making something readable that was not, adding a new
place uploads are stored, or extending what the server can decrypt.

### 3. No new dependencies without a reason

The frontend is React and Vite with no UI framework, and the end-to-end crypto is
vanilla Web Crypto with zero dependencies. The backend is deliberately plain
FastAPI and SQLAlchemy. New packages are a supply-chain surface and a maintenance
cost, so bring a reason.

### 4. Keep changes focused

One concern per pull request. Do not bundle a refactor with a feature, and do not
reformat files you are not otherwise touching. Unrelated whitespace churn makes a
diff impossible to review.

---

## Getting it running

You need **Docker**, **Python 3.13** and **Node 22+**.

> **Python 3.13 specifically.** On 3.14 there are no prebuilt wheels for
> `pydantic-core` or `asyncpg`, so pip tries to compile Rust and C and fails. If
> your default `python` is newer, create the virtualenv with `py -3.13` (Windows)
> or `python3.13` (Linux and macOS).

```bash
# 1. datastores
docker compose up -d                 # Postgres + Redis

# 2. backend
cd backend
py -3.13 -m venv .venv && . .venv/Scripts/activate   # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt   # runtime + test deps
cp .env.example .env                 # dev defaults are fine
alembic upgrade head                 # create the schema
uvicorn app.main:app --reload --port 8000

# 3. frontend, in a second shell
cd frontend
npm install
npm run dev                          # http://localhost:5173
```

The first backend start seeds the read-only `#whatsnew` channel. Register an
account in the app and you are in.

For a realistic dataset to develop against (four users, a populated channel, a
DM and a group), run `python backend/demo_setup.py` with the backend up.

---

## Tests

```bash
cd backend
pytest -q
```

Two things to know:

1. **The suite runs against real Postgres and Redis**, the same containers dev
   uses, because the things worth testing here (advisory locks, cascade deletes,
   rate limiting, token revocation, encrypted-upload opacity) do not behave
   realistically against mocks. Start `docker compose up -d` first.
2. **Tests run with `AUTO_CREATE_TABLES=0`**, so the schema comes from migrations.
   If you added one, run `alembic upgrade head` before the suite or every test
   will fail on a missing column.

The frontend has its own suite:

```bash
cd frontend
npm test          # or npm run test:watch while you work
```

It is Vitest over jsdom, so it needs no database and finishes in about a second.
It covers the places where being wrong is expensive rather than trying to cover
everything: the end-to-end crypto, the server-origin and URL sanitising, and the
message renderer, which turns text written by other people into DOM.

The crypto tests run against Node's real WebCrypto, not a mock. Testing that
module against a stub would produce a green suite that says nothing about the
guarantee it exists to protect.

**CI runs both suites on every push and pull request.** The backend runs against
Postgres and Redis service containers, with the schema applied by Alembic rather
than `create_all`, so a broken migration fails the build too. A third job
byte-compiles the backend, builds the frontend and builds both Docker images.

Run them locally before pushing anyway: together they take about ten seconds,
and it is a much shorter feedback loop than waiting on CI.

New behaviour should come with a test. Bug fixes should come with the test that
would have caught the bug.

---

## Database migrations

Schema changes go through Alembic:

```bash
cd backend
alembic revision --autogenerate -m "short description"
alembic upgrade head
```

**Read the generated file before committing it.** Autogenerate is a starting
point, not an answer.

The trap worth knowing: **a migration that adds a `NOT NULL` column to an
existing table will fail on any database that has rows in it.** Add the column
with a `server_default`, then drop the default in a second step:

```python
op.add_column("users", sa.Column("bio", sa.String(), nullable=False, server_default=""))
op.alter_column("users", "bio", server_default=None)
```

Always test a migration against your dev database, which has data in it, rather
than only the empty scratch database autogenerate compares against.

---

## Code conventions

There is no linter or formatter configured, so the rule is: **match the code
around you.**

**Backend (Python).** Type-hinted function signatures. Pydantic schemas live in
`app/schemas.py`, routes in `app/routers/`. Shared permission checks belong in a
helper rather than copy-pasted into each endpoint; see `_require_moderator`,
`_require_owner` and `_active_channel` in `routers/channels.py`.

**Frontend (JavaScript and JSX).** Function components with hooks. No class
components, no state library, no UI kit.

**Security invariants that are not negotiable:**

- **Never use `dangerouslySetInnerHTML`.** Output encoding is the primary XSS
  defence, and React gives it for free. `MessageContent.jsx` tokenises mentions
  and links into React elements rather than building HTML strings; extend that
  pattern.
- **All free text goes through `app/sanitize.py`** on the way in. It strips
  control, zero-width and bidi characters and normalises to NFC.
- **`is_admin` is read from the database per request** and is never carried in a
  JWT. Do not add it to a token.
- **Never commit a `.env` file.** Only the `.example` files are tracked. The same
  goes for keys, tokens, real hostnames and backup credentials.

---

## Branches, commits and pull requests

**Branch model.** `main` is the deployment branch: pushing it to the production
remote is what ships. Day-to-day work happens on **`dev`**.

- Branch from `dev`, and open your pull request **against `dev`**.
- Pull requests against `main` will be asked to retarget unless they are an
  urgent production fix.

**Commits.** Imperative subject line under about 72 characters, then a body that
explains *why* rather than restating the diff:

```
Batch the per-channel unread queries

list_channels ran ~5 queries per channel, so a directory of 40 channels
cost 200 round trips. _channel_stats now does it in four grouped queries
regardless of channel count.
```

**Pull requests** should say what changed, why, and how you tested it. If it is
user-visible, include a screenshot. If it touches the security boundary, say so
explicitly (see ground rule 2).

---

## When your change is user-visible

Documentation drifts fast here, because several surfaces describe the same
features. If you add or change something a user would notice, update these in the
same pull request:

| Surface | File |
|---|---|
| User guide | `docs/USER_GUIDE.md` **and** its HTML mirror `frontend/public/guide.html` |
| Developer guide (API changes) | `docs/DEVELOPER_GUIDE.md` **and** `frontend/public/developers.html` |
| Privacy claims | `frontend/public/privacy.html` |
| Feature list | `README.md` |

The two guides are hand-maintained pairs: the markdown is for the repo, the HTML
is what the running app serves. Updating only one is the most common review
comment on this project. Both guides carry a "Covers Open Relay vX.Y.Z" line so
it is obvious when they have fallen behind.

---

## Releases

Maintainer territory, recorded here so the steps are not folklore.

1. Bump the version in **both** places, kept in sync:
   - `frontend/src/version.js` (`APP_VERSION`)
   - `backend/app/main.py` (`APP_VERSION`)
2. Add an entry to `WHATSNEW_POSTS` in `backend/app/seed.py`. It is posted to the
   in-app `#whatsnew` channel on startup, so write it for users, not developers.
3. Update the "Covers Open Relay vX.Y.Z" line in both guides.
4. Merge `dev` into `main` and push. Pushing `main` to GitHub creates the version
   marker release automatically; pushing it to the production remote deploys, and
   migrations run on container start.
5. **If the frontend changed, cut a desktop build.** The desktop client bundles a
   snapshot of the web UI, so installed clients do not get frontend changes until
   a new build ships. Bump the version in `desktop/package.json`,
   `desktop/src-tauri/tauri.conf.json`, `desktop/src-tauri/Cargo.toml` and
   `Cargo.lock`, then tag `desktop-v<version>`. The build produces a draft
   release; publish it and mark it "Latest" so the auto-updater picks it up.

Desktop releases hold the GitHub "Latest" badge because the updater reads
`releases/latest/download/latest.json`. Web releases are published as
pre-releases so they cannot steal it.

---

## Writing style

The project's voice is plain, specific and honest, in documentation as much as in
the interface. A few house preferences:

- Prefer a colon, semicolon or full stop to an em dash.
- Say what something does, not that it is powerful or seamless.
- When describing a protection, describe its limit in the same breath. That
  honesty is the product.

---

## Questions

Open an issue. For anything security-related, use the private reporting route at
the top of this document instead.
