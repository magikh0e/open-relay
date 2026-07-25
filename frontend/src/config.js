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

function resolveServer() {
  if (typeof window !== "undefined" && window.__RELAY_SERVER__)
    return normalize(window.__RELAY_SERVER__);
  try {
    const stored = localStorage.getItem("relay_server");
    if (stored) return normalize(stored);
  } catch {
    /* localStorage may be unavailable in some shells; fall through */
  }
  if (import.meta.env && import.meta.env.VITE_API_BASE)
    return normalize(import.meta.env.VITE_API_BASE);
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

// Resolve a possibly-relative URL handed back by the server (e.g. an attachment
// path "/api/uploads/<id>") against the configured origin, so it loads from any
// client. Absolute, data: and blob: URLs are returned untouched.
export function resolveUrl(u) {
  if (!u) return u;
  if (/^(https?:|data:|blob:)/.test(u)) return u;
  if (SERVER && u.startsWith("/")) return `${SERVER}${u}`;
  return u;
}
