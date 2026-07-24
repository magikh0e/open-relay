# Open Relay — Desktop Client Build Plan

A sequenced plan for a desktop client that wraps the existing React/Vite SPA in
a webview pointed at a remote server. Framework: **Tauri** (recommended);
Electron deltas are noted at each fork. This is a planning doc, not a
commitment — nothing here is built yet.

_Open Relay and this plan by **magikh0e**. Free software under the GNU GPL-3.0._

## Goal & v1 scope

A lightweight desktop app that runs the current web UI against any Open Relay
server. Because it's a webview, the browser-only features already work
unchanged: WebCrypto E2EE, canvas EXIF-strip, WebSocket, fetch.

**In scope for v1:**
- Wrap the Vite SPA; point it at a server via `window.__RELAY_SERVER__`.
- A server picker (enter/switch server URL).
- Password login (no OAuth yet).
- Native notifications fired from WebSocket events while the app is open.
- Token storage in the OS keychain.
- Tray icon, single-instance, remembered window size/position.
- Auto-update.

**Deferred to later:**
- OAuth (Discord) via system browser + custom-scheme deep link.
- Background push (app fully closed) via APNs/FCM.
- Per-server multi-account switching (the web-side groundwork exists via
  `config.js`; the client just holds N `{server, token}` sessions).

## Already done (foundation)

- `frontend/src/config.js` resolves the server origin, checking
  `window.__RELAY_SERVER__` first. **The desktop shell injects that global and
  the SPA needs no change.** REST (`API_BASE`), WebSocket (`wsBase()`), and
  attachment URLs (`resolveUrl()`) all follow it. Verified: same-origin web
  build is byte-for-byte unchanged.

---

## Phase 1 — Scaffold Tauri around the existing app

- [ ] Install the Rust toolchain + Tauri CLI. Add Tauri to the existing
      frontend (`npm create tauri-app` in a way that reuses `frontend/`, or add
      `@tauri-apps/cli` and a `src-tauri/` folder manually).
- [ ] Point Tauri at the Vite dev server and build output:
      - `build.devUrl` → `http://localhost:5173`
      - `build.frontendDist` → `../dist`
      - `build.beforeDevCommand` → `npm run dev`, `beforeBuildCommand` → `npm run build`
- [ ] Confirm `npm run tauri dev` loads the app in the native webview and the UI
      renders. At this point it still talks same-origin (no server yet).

> **Electron delta:** use `electron-vite` for the same dev/build wiring; add a
> `main.js` (main process) and `preload.js`.

## Phase 2 — Origin injection & server picker

- [ ] Inject `window.__RELAY_SERVER__` **before** the SPA loads, via Tauri's
      webview `initialization_script`. Source the value from persisted config
      (default empty → show the picker).
- [ ] Build a minimal first-run screen: enter a server URL, validate it with
      `GET <origin>/api/health` (expect `{"app":"Open Relay"}`), persist it.
- [ ] "Switch server" action: clear tokens + cached E2EE key, update the stored
      origin, reload. (Tokens/keys are per-server — see `config.js` notes.)

> **Electron delta:** set `window.__RELAY_SERVER__` in `preload.js`.

## Phase 3 — Server-side changes (small)

- [ ] Add the shell's origin to `CORS_ORIGINS` (`backend/app/config.py` /
      deployment env):
      - **Tauri:** `tauri://localhost` and `http://tauri.localhost` (Windows).
      - **Electron:** register a custom `app://` protocol and add that origin
        (avoids the `file://` null-origin problem).
- [ ] No WebSocket change needed — `/ws` is not CORS-gated.
- [ ] Note: auth is bearer-token, not cookies, so credentialed CORS isn't
      actually required; listing the specific origin is enough.

## Phase 4 — Auth & secure token storage

- [ ] Password login already works through the SPA (`POST /api/auth/login`).
      Nothing new to build for the happy path.
- [ ] Move tokens out of `localStorage` into the OS keychain:
      - **Tauri:** `tauri-plugin-store` for config + a keychain plugin
        (`keyring`/`stronghold`) for the refresh token.
      - **Electron:** `safeStorage` or `keytar`.
- [ ] Keep the access token in memory; persist only the refresh token securely.
      Reuse the existing refresh flow (`refreshAccess()` in `api.js`).

## Phase 5 — Native notifications from WebSocket

- [ ] While the app runs, the WS is connected — subscribe to `message`,
      `dm_opened`, and mention events (`ChatShell.onEvent`) and raise a native
      notification when the window is unfocused/backgrounded.
- [ ] Notification payload carries only who/where (mirror the Web Push policy —
      never message text, to preserve E2EE).
- [ ] Click → focus the window and route to the channel/DM.
- [ ] Unread badge on the tray/dock icon.

> No push service needed for v1 — this covers the "app open" case entirely.

## Phase 6 — Desktop niceties

- [ ] Tray icon with show/hide + quit; close-to-tray option.
- [ ] Single-instance lock (focus the existing window instead of launching a
      second) — needed later for deep-link OAuth anyway.
- [ ] Persist and restore window size/position.
- [ ] App icons per platform (reuse `openrelay.webp` / existing PWA icons).

## Phase 7 — Auto-update

- [ ] **Tauri updater plugin:** host a signed `latest.json` + artifacts (the
      VPS can serve these), sign with the Tauri updater key.
- [ ] **Electron:** `electron-updater` against a static feed / GitHub releases.
- [ ] Wire an in-app "update available → restart" prompt.

## Phase 8 — Packaging & signing

- [ ] Build targets: Windows (`.msi`/NSIS), macOS (`.dmg` — notarization if
      distributing outside a controlled group), Linux (`.AppImage`/`.deb`).
- [ ] Code signing where required (Windows cert, Apple Developer ID). Can ship
      unsigned to a trusted group initially with a documented Gatekeeper/
      SmartScreen bypass.
- [ ] **Validate the Linux WebKitGTK build early** — it's the one webview that
      can surprise you; smoke-test WebCrypto (E2EE unlock), WS reconnect, and
      attachment fetch there before investing further.

---

## Deferred — OAuth deep link (when wanted)

The web SSO flow redirects to `PUBLIC_BASE_URL/#access=…`, a web origin the app
can't catch. Desktop pattern:

1. Register a custom scheme (`openrelay://`) — `tauri-plugin-deep-link` /
   Electron `setAsDefaultProtocolClient`.
2. Open the **system browser** to `/api/auth/oauth/discord/start`.
3. **Server change:** allow a desktop redirect target so the callback lands on
   `openrelay://auth#access=…&refresh=…` instead of the web URL (e.g. keyed off
   a `state`/client-type param).
4. App catches the deep link (single-instance required), parses the fragment,
   stores tokens.

## Deferred — background push (app closed)

Needs a real push service (APNs on macOS, FCM/WNS elsewhere) and a server
endpoint per platform, paralleling the existing Web Push. Only worth it once
"notify me when the app is closed" becomes a requirement.

---

## Testing checklist (per platform)

- [ ] First-run server picker → `/api/health` validation → connect.
- [ ] Login, send/receive a message (WS echo), reactions, edits.
- [ ] E2EE: set passphrase, encrypt a DM, verify safety numbers, decrypt an
      attachment (proves WebCrypto in that OS's webview).
- [ ] Reconnect after network drop / sleep (WS backoff + token refresh).
- [ ] Native notification on background message → click focuses + routes.
- [ ] Token persists across restart (keychain), "switch server" clears state.
- [ ] Auto-update applies a signed release.

## Open decisions

- Framework (Tauri vs Electron) — **not final**; plan assumes Tauri.
- Multi-account/multi-server in v1 or later (groundwork exists).
- Distribution channel + whether to code-sign for v1 or ship to a trusted
  group unsigned first.
