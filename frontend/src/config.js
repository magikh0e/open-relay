// Where the backend lives.
//
// The web build is served by the same host that answers /api and /ws, so the
// default — same origin — keeps that deployment byte-for-byte unchanged. A
// desktop client loads its UI from a local origin (tauri://, app://, a bundled
// file), so it must be told the server's public origin instead. This module is
// the one place that decision is made; everything else asks it.
//
// Resolution order, first hit wins:
//   1. window.__RELAY_SERVER__       — injected by a native shell at startup
//   2. localStorage "relay_server"   — chosen in an in-app server picker
//   3. import.meta.env.VITE_API_BASE — baked in at build time
//   4. ""                            — same origin (the web deployment)
//
// A resolved value is an absolute origin like "https://chat.openrelay.pl":
// no trailing slash, no /api suffix. The public paths (/api, /ws) are fixed by
// the reverse proxy and are the same from every client — only the origin moves.
//
// The value is read once at load. Changing servers means changing localStorage
// and reloading, which is correct anyway: tokens and cached keys are per-server.

function normalize(base) {
  return base ? base.replace(/\/+$/, "") : "";
}

// Only ever accept an http(s) origin as the server. The value can come from
// localStorage or an injected global, so this keeps a hostile value (a
// "javascript:" URL, say) from ever reaching API_BASE and a navigation sink.
function httpOrigin(v) {
  const n = normalize(v);
  return /^https?:\/\//i.test(n) ? n : "";
}

function resolveServer() {
  if (typeof window !== "undefined" && window.__RELAY_SERVER__) {
    const v = httpOrigin(window.__RELAY_SERVER__);
    if (v) return v;
  }
  try {
    const v = httpOrigin(localStorage.getItem("relay_server"));
    if (v) return v;
  } catch {
    /* localStorage may be unavailable in some shells; fall through */
  }
  if (import.meta.env && import.meta.env.VITE_API_BASE) {
    const v = httpOrigin(import.meta.env.VITE_API_BASE);
    if (v) return v;
  }
  return "";
}

// The backend's origin. "" means "same origin as this page" (the web build).
export const SERVER = resolveServer();

// Base for REST calls. Caddy exposes the backend under /api publicly, so a
// remote origin still speaks to it there.
export const API_BASE = `${SERVER}/api`;

// ws(s):// origin for the live socket. Mirrors the page's scheme same-origin,
// and maps http→ws / https→wss for a configured remote server.
export function wsBase() {
  if (SERVER) return SERVER.replace(/^http/, "ws");
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}`;
}

// --- In-app server picker helpers -----------------------------------------
// The picker writes localStorage and reloads; SERVER above is read once at load,
// so these never mutate live state, they just stage the next load.

// The override the picker has stored ("" when none, i.e. the build default).
export function storedServer() {
  try {
    return normalize(localStorage.getItem("relay_server") || "");
  } catch {
    return "";
  }
}

// A human label for the server currently in effect, for the picker UI.
export function serverLabel() {
  return SERVER || (typeof location !== "undefined" ? location.origin : "");
}

// Stage a different server for the next load. Pass "" to clear the override and
// fall back to the build default (same origin on web, DEFAULT_SERVER on
// desktop). The caller must reload; tokens and cached keys are per-server, so a
// switch is effectively a fresh session.
export function setServer(url) {
  const v = normalize((url || "").trim());
  try {
    if (v) localStorage.setItem("relay_server", v);
    else localStorage.removeItem("relay_server");
  } catch {
    /* localStorage may be unavailable; nothing to persist */
  }
  return v;
}

// Servers the user has saved for quick switching, as normalized http(s)
// origins. The build default is reachable separately (setServer("")).
export function savedServers() {
  try {
    const arr = JSON.parse(localStorage.getItem("relay_servers") || "[]");
    return Array.isArray(arr) ? arr.filter((s) => typeof s === "string") : [];
  } catch {
    return [];
  }
}

// Add a server to the saved list (or move it to the front). Non-http(s) values
// are ignored. Returns the updated list.
export function addSavedServer(url) {
  const v = normalize(url);
  if (!/^https?:\/\//i.test(v)) return savedServers();
  const list = [v, ...savedServers().filter((s) => s !== v)].slice(0, 12);
  try {
    localStorage.setItem("relay_servers", JSON.stringify(list));
  } catch {
    /* ignore */
  }
  return list;
}

export function removeSavedServer(url) {
  const v = normalize(url);
  const list = savedServers().filter((s) => s !== v);
  try {
    localStorage.setItem("relay_servers", JSON.stringify(list));
  } catch {
    /* ignore */
  }
  return list;
}

// Whether a stored session exists for a server (its per-server access token is
// present), so the picker can flag which instances you're already signed into.
// The token bucket is keyed by origin, matching api.js.
export function hasSession(url) {
  try {
    return !!localStorage.getItem(`chat_access:${normalize(url)}`);
  } catch {
    return false;
  }
}

// Schemes we'll let reach an <a href> / <img src>. `javascript:` (and anything
// else) is dropped, so a hostile server (any server, in the multi-server model)
// can't hand back a URL that runs on click.
const SAFE_URL_SCHEMES = new Set(["http:", "https:", "blob:", "data:"]);

// Resolve a possibly-relative URL handed back by the server (e.g. an attachment
// path "/api/uploads/<id>") against the configured origin, so it loads from any
// client. Parsing through the URL API and gating on the real scheme both keeps
// the value safe and reads as a sanitizer to static analysis.
export function resolveUrl(u) {
  if (!u) return u;
  // Protocol-relative ("//host/…") resolves to an arbitrary host; don't trust it.
  if (u.startsWith("//")) return "";
  const base = SERVER || (typeof location !== "undefined" ? location.href : undefined);
  let parsed;
  try {
    parsed = new URL(u, base);
  } catch {
    return "";
  }
  return SAFE_URL_SCHEMES.has(parsed.protocol) ? parsed.href : "";
}
