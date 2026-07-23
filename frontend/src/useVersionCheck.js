import { useEffect, useState } from "react";

// The build id baked in at build time (replaced by Vite's `define`).
const CURRENT = typeof __BUILD_ID__ !== "undefined" ? __BUILD_ID__ : "dev";

// Polls /version.json and flags when the deployed build differs from the one
// this tab is running — so we can prompt the user to refresh.
export function useVersionCheck(intervalMs = 60000) {
  const [updateAvailable, setUpdateAvailable] = useState(false);

  useEffect(() => {
    let stopped = false;

    async function check() {
      try {
        const res = await fetch(`/version.json?t=${Date.now()}`, {
          cache: "no-store",
        });
        if (!res.ok) return; // not deployed (e.g. dev server) — ignore
        const { build } = await res.json();
        if (build && build !== CURRENT && !stopped) setUpdateAvailable(true);
      } catch {
        /* offline or unreachable — ignore */
      }
    }

    check();
    const id = setInterval(check, intervalMs);
    // Re-check when the user returns to the tab.
    const onFocus = () => check();
    window.addEventListener("focus", onFocus);

    return () => {
      stopped = true;
      clearInterval(id);
      window.removeEventListener("focus", onFocus);
    };
  }, [intervalMs]);

  return updateAvailable;
}
