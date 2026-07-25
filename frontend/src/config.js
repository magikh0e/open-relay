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

// Resolve a possibly-relative URL handed back by the server (e.g. an attachment
// path "/api/uploads/<id>") against the configured origin, so it loads from any
// client. Only safe-to-load schemes are allowed through: a hostile server (any
// server, in the multi-server model) could otherwise hand back a
// "javascript:..." URL that would run when used as an <a href>. Anything that
// isn't an http(s)/blob/data URL or a server-relative path is dropped.
export function resolveUrl(u) {
  if (!u) return u;
  // Protocol-relative ("//host/…") can point anywhere; don't trust it.
  if (u.startsWith("//")) return "";
  if (/^(https?:|blob:|data:)/i.test(u)) return u;
  if (u.startsWith("/")) return SERVER ? `${SERVER}${u}` : u;
  return "";
}
