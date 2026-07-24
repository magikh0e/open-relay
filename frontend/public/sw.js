// Service worker for Open Relay.
//
// Deliberately conservative. A caching service worker that gets this wrong
// pins users to a stale build indefinitely and is painful to undo, so:
//
//   * API, WebSocket and version.json requests are never touched — the update
//     banner polls version.json, and caching it would break update detection.
//   * Only /assets/* is cached aggressively, and only because Vite content-
//     hashes those filenames, making them immutable by construction.
//   * Navigations are network-first; the cache is a fallback for offline, not
//     a source of truth.
//   * A new worker activates immediately rather than waiting for every tab to
//     close, so a bad deploy can always be corrected by the next one.

const CACHE = "openrelay-v2";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((c) => c.addAll(["/"]).catch(() => {}))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
      await self.clients.claim();
    })()
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live data and update detection must always hit the network.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/ws") ||
    url.pathname === "/version.json"
  ) {
    return;
  }

  // Content-hashed build output: safe to serve from cache indefinitely.
  if (url.pathname.startsWith("/assets/")) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ||
          fetch(request).then((res) => {
            if (res.ok) {
              const copy = res.clone();
              caches.open(CACHE).then((c) => c.put(request, copy));
            }
            return res;
          })
      )
    );
    return;
  }

  // Everything else (documents, icons): network first, cache as a fallback so
  // the app shell still renders offline.
  event.respondWith(
    fetch(request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(request, copy));
        }
        return res;
      })
      .catch(async () => {
        const hit = await caches.match(request);
        if (hit) return hit;
        if (request.mode === "navigate") {
          const shell = await caches.match("/");
          if (shell) return shell;
        }
        return Response.error();
      })
  );
});

// --- push notifications ----------------------------------------------------
//
// Payloads carry only who and where — never message text — so nothing
// sensitive passes through the push service or sits in the notification.

self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch {
    /* malformed payload: fall through to the generic message below */
  }
  const title = data.title || "Open Relay";
  event.waitUntil(
    self.registration.showNotification(title, {
      body: data.body || "New activity",
      icon: "/icon-192.png",
      badge: "/icon-192.png",
      // Same tag per channel so a burst collapses into one notification
      // instead of stacking up.
      tag: data.tag || "openrelay",
      renotify: true,
      data: { url: data.url || "/" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil(
    (async () => {
      const all = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      // Focus an existing tab rather than piling up new ones.
      for (const client of all) {
        if (client.url.includes(self.location.origin)) {
          await client.focus();
          if ("navigate" in client) await client.navigate(target);
          return;
        }
      }
      await self.clients.openWindow(target);
    })()
  );
});
