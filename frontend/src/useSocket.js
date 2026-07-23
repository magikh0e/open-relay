import { useEffect, useRef } from "react";
import { refreshAccess, tokens } from "./api.js";

// Opens one WebSocket for the session and fans server events out to a set of
// handlers. Reconnects with backoff and sends periodic pings for keepalive.
export function useSocket(enabled, onEvent) {
  const wsRef = useRef(null);
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    if (!enabled) return;

    let closedByUs = false;
    let retry = 0;
    let pingTimer = null;

    async function connect() {
      // Refresh the access token first so the WS handshake never fails on an
      // expired token (the socket is long-lived; tokens expire in ~30 min).
      await refreshAccess();
      if (closedByUs) return;
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const url = `${proto}://${location.host}/ws?token=${encodeURIComponent(
        tokens.access
      )}`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        retry = 0;
        pingTimer = setInterval(() => {
          if (ws.readyState === WebSocket.OPEN)
            ws.send(JSON.stringify({ type: "ping" }));
        }, 25000);
      };

      ws.onmessage = (ev) => {
        try {
          const msg = JSON.parse(ev.data);
          handlerRef.current?.(msg);
        } catch {
          /* ignore malformed frames */
        }
      };

      ws.onclose = () => {
        clearInterval(pingTimer);
        if (closedByUs) return;
        retry += 1;
        const delay = Math.min(1000 * 2 ** retry, 15000);
        setTimeout(connect, delay);
      };
    }

    connect();

    return () => {
      closedByUs = true;
      clearInterval(pingTimer);
      wsRef.current?.close();
    };
  }, [enabled]);

  function send(obj) {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
  }

  return { send };
}
