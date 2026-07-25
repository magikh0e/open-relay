import { useState } from "react";
import { serverLabel, storedServer, setServer } from "../config.js";

// Point the app at any Open Relay instance. Writes the chosen origin to
// localStorage (read by config.js on next load) and reloads. Because login and
// encryption keys are per-server, switching is a fresh session.
export default function ServerPicker({ onClose }) {
  const current = serverLabel();
  const [url, setUrl] = useState(storedServer());
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [warned, setWarned] = useState(false);

  function normalizeInput(v) {
    let s = (v || "").trim();
    if (!s) return "";
    if (!/^https?:\/\//i.test(s)) s = "https://" + s; // assume TLS if unscheme'd
    return s.replace(/\/+$/, "");
  }

  async function connect() {
    const target = normalizeInput(url);
    if (!target) {
      // Empty input means "reset to the build default".
      setServer("");
      location.reload();
      return;
    }
    try {
      new URL(target);
    } catch {
      setMsg("That doesn't look like a valid address.");
      return;
    }
    if (!/^https?:\/\//i.test(target)) {
      setMsg("Enter an http(s) address.");
      return;
    }
    setBusy(true);
    setMsg("");
    // Best-effort reachability probe. Cross-origin CORS can block it even when
    // the server is perfectly fine, so a failure only warns; it never blocks.
    let reachable = false;
    try {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), 4000);
      const res = await fetch(`${target}/api/health`, { signal: ctrl.signal });
      clearTimeout(t);
      reachable = res.ok;
    } catch {
      reachable = false;
    }
    if (!reachable && !warned) {
      setWarned(true);
      setBusy(false);
      setMsg(
        "Couldn't verify that server (it may still work, or it blocks checks). Connect anyway?"
      );
      return;
    }
    setServer(target);
    location.reload();
  }

  function reset() {
    setServer("");
    location.reload();
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Choose server</h3>
          <button className="link" onClick={onClose}>
            ✕
          </button>
        </div>
        <p className="muted small">
          Point this app at any Open Relay instance. Your login and encryption
          keys are per-server, so switching signs you out of this one.
        </p>
        <div className="muted small">
          Currently: <code>{current}</code>
        </div>
        <input
          type="url"
          placeholder="https://chat.example.com"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setWarned(false);
            setMsg("");
          }}
          onKeyDown={(e) => e.key === "Enter" && !busy && connect()}
          autoFocus
        />
        {msg && <div className="muted small">{msg}</div>}
        <button className="primary" disabled={busy} onClick={connect}>
          {busy ? "Checking…" : warned ? "Connect anyway" : "Connect"}
        </button>
        {storedServer() && (
          <button className="mini ghost" onClick={reset}>
            Reset to default
          </button>
        )}
      </div>
    </div>
  );
}
