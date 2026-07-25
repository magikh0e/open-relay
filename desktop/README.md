# Open Relay — desktop client (Tauri)

A thin Tauri shell that runs the existing web UI (`../frontend`) in a native
webview, pointed at a remote Open Relay server. Because it's a webview, the
browser features already work unchanged: WebCrypto E2EE, canvas EXIF-strip,
WebSocket, fetch.

It lives in its own top-level workspace (**not** inside `frontend/`) so the
Tauri toolchain never touches the web build — the production web image's
`npm ci` stays lean and unaffected.

This is the **v1 scaffold** — it opens the app against a server and nothing
more yet. See `../docs/DESKTOP_CLIENT_PLAN.md` for the full roadmap (server
picker, keychain tokens, native notifications, tray, auto-update, OAuth).

## Prerequisites

- **Node** (already required for the web build).
- **Rust toolchain** — install from <https://rustup.rs>.
- **Windows only:** the **MSVC C++ build tools** (Visual Studio Build Tools →
  "Desktop development with C++"). WebView2 ships with Windows 11.
- **Linux:** `webkit2gtk` + `libsoup` dev packages (see the Tauri prerequisites
  page for your distro).

## Run it

From this `desktop/` directory:

```bash
npm install          # pulls in @tauri-apps/cli (first time only)
npm run tauri:dev    # starts the frontend's Vite, compiles the Rust shell, opens the window
```

`tauri:dev` runs the frontend dev server for you (`npm --prefix ../frontend run
dev`) and waits for Vite on **http://localhost:5173** (the `devUrl` in
`src-tauri/tauri.conf.json`) — make sure no other Vite instance is holding that
port, or update `devUrl` to match.

Production bundle (installers per OS):

```bash
npm run tauri:build
```

Before the first release build, generate the full platform icon set (this
scaffold ships only a single `src-tauri/icons/icon.png`):

```bash
npx tauri icon ../frontend/public/icon-512.png
```

…then list the generated files in `src-tauri/tauri.conf.json` → `bundle.icon`.

## Which server it talks to

Resolution order (handled by `../frontend/src/config.js`):

1. `window.__RELAY_SERVER__` — injected by this shell before the page loads
   (see `src-tauri/src/lib.rs`). Seeded from the in-app stored value, else the
   default.
2. `localStorage "relay_server"` — the in-app server picker (roadmap Phase 2).
3. Same-origin — not useful in a desktop webview, hence the injection.

The default server is `DEFAULT_SERVER` in `src-tauri/src/lib.rs`
(`https://chat.openrelay.pl`). Override at launch with the `RELAY_SERVER` env var,
or repoint the constant.

## Server-side requirement (CORS)

The app makes cross-origin calls to the server, so the server's `CORS_ORIGINS`
**must include the webview's origin** or every request fails (the classic
"Failed to fetch"). The origin differs by mode:

| Mode | Webview origin |
|---|---|
| `tauri:build` on Windows | `http://tauri.localhost` |
| `tauri:build` on macOS/Linux | `tauri://localhost` |
| **`tauri:dev`** (any OS) | `http://localhost:5173` (the Vite dev server) |

So allow all three on the VPS `.env.prod` — the last one only matters while
developing against a remote server, and can be dropped afterwards:

```
CORS_ORIGINS=https://chat.openrelay.pl,tauri://localhost,http://tauri.localhost,http://localhost:5173
```

The WebSocket (`/ws`) is not CORS-gated, so it needs no change.

## Layout

```
desktop/
  package.json          Tauri CLI + npm scripts (separate from the web project)
  src-tauri/
    Cargo.toml          Rust crate + release size profile
    build.rs            tauri-build hook
    tauri.conf.json     app metadata, window, bundle, dev/build wiring
    capabilities/       Tauri v2 permission grants
    icons/icon.png      placeholder icon (regenerate with `tauri icon`)
    src/
      main.rs           thin binary entry point
      lib.rs            window creation + server-origin injection
```
