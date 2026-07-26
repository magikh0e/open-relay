import { api } from "./api.js";

// Browser push subscription management.
//
// The server sends only who/where in a push payload (never message text), so
// nothing sensitive passes through the push service.

function urlB64ToUint8Array(base64) {
  const padded = base64.padEnd(
    base64.length + ((4 - (base64.length % 4)) % 4),
    "="
  );
  const raw = atob(padded.replace(/-/g, "+").replace(/_/g, "/"));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

export function pushSupported() {
  return (
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window
  );
}

export function pushPermission() {
  return pushSupported() ? Notification.permission : "unsupported";
}

/** Is this browser currently subscribed? */
export async function isSubscribed() {
  if (!pushSupported()) return false;
  const reg = await navigator.serviceWorker.getRegistration();
  if (!reg) return false;
  return !!(await reg.pushManager.getSubscription());
}

/** Ask permission, subscribe, and register with the server. */
export async function enablePush() {
  if (!pushSupported()) throw new Error("This browser can't do notifications.");

  const permission = await Notification.requestPermission();
  if (permission !== "granted") {
    throw new Error(
      permission === "denied"
        ? "Notifications are blocked for this site. You'll need to allow them in your browser settings."
        : "Notifications weren't enabled."
    );
  }

  const reg = await navigator.serviceWorker.ready;
  const { public_key } = await api("/push/key", { auth: false });
  const sub =
    (await reg.pushManager.getSubscription()) ||
    (await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToUint8Array(public_key),
    }));

  const json = sub.toJSON();
  await api("/push/subscribe", {
    method: "POST",
    body: {
      endpoint: sub.endpoint,
      p256dh: json.keys.p256dh,
      auth: json.keys.auth,
    },
  });
  return true;
}

export async function disablePush() {
  const reg = await navigator.serviceWorker.getRegistration();
  const sub = reg && (await reg.pushManager.getSubscription());
  if (!sub) return;
  // Tell the server first — once unsubscribed locally we lose the endpoint.
  await api("/push/unsubscribe", {
    method: "POST",
    body: { endpoint: sub.endpoint, p256dh: "x", auth: "x" },
  }).catch(() => {});
  await sub.unsubscribe();
}
