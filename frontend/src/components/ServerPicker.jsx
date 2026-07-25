import { useState } from "react";
import {
  serverLabel,
  savedServers,
  addSavedServer,
  removeSavedServer,
  hasSession,
  setServer,
} from "../config.js";
import { useDialog } from "../useDialog.js";

// Point the app at any Open Relay instance and keep a list to switch between.
// Switching writes localStorage (read by config.js on next load) and reloads;
// sessions are namespaced per server, so switching to one you're already
// signed into is instant, and others just ask you to sign in.
export default function ServerPicker({ onClose }) {
  const dialogRef = useDialog(onClose);
  const active = serverLabel(); // the origin currently in use
  // Make sure the active server is listed, so switching is symmetric.
  const [list, setList] = useState(() => addSavedServer(active));
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [warned, setWarned] = useState(false);

  function switchTo(target) {
    if (target === active) return;
    setServer(target);
    location.reload();
  }

  function remove(target) {
    setList(removeSavedServer(target));
  }

  function normalizeInput(v) {
    let s = (v || "").trim();
    if (!s) return "";
    if (!/^https?:\/\//i.test(s)) s = "https://" + s; // assume TLS if unscheme'd
    return s.replace(/\/+$/, "");
  }

  async function addAndConnect() {
    const target = normalizeInput(url);
    if (!target) return;
    try {
      new URL(target);
    } catch {
      setMsg("That doesn't look like a valid address.");
      return;
    }
    setBusy(true);
    setMsg("");
    // Best-effort reachability probe; CORS can block it even when the server is
    // fine, so a failure only warns.
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
        "Couldn't verify that server (it may still work, or it blocks checks). Add anyway?"
      );
      return;
    }
    addSavedServer(target);
    switchTo(target);
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Servers"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Servers</h3>
          <button className="link" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        <p className="muted small">
          Point this app at any Open Relay server. Sessions are kept per
          server, so switching to one you're signed into is instant; the rest
          ask you to sign in.
        </p>
        <ul className="server-list">
          {list.map((s) => (
            <li
              key={s}
              className={`server-item${s === active ? " current" : ""}`}
            >
              <button
                className="server-main"
                disabled={s === active}
                onClick={() => switchTo(s)}
                title={s === active ? "Current server" : `Switch to ${s}`}
              >
                <span className="server-name">
                  {s.replace(/^https?:\/\//, "")}
                </span>
                {s === active ? (
                  <span className="server-badge">current</span>
                ) : hasSession(s) ? (
                  <span className="server-badge in">signed in</span>
                ) : null}
              </button>
              {s !== active && (
                <button
                  className="server-remove"
                  title="Remove from list"
                  aria-label={`Remove ${s}`}
                  onClick={() => remove(s)}
                >
                  ✕
                </button>
              )}
            </li>
          ))}
        </ul>
        <input
          type="url"
          placeholder="Add a server: https://chat.example.com"
          value={url}
          onChange={(e) => {
            setUrl(e.target.value);
            setWarned(false);
            setMsg("");
          }}
          onKeyDown={(e) => e.key === "Enter" && !busy && addAndConnect()}
        />
        {msg && <div className="muted small">{msg}</div>}
        <button
          className="primary"
          disabled={busy || !url.trim()}
          onClick={addAndConnect}
        >
          {busy ? "Checking…" : warned ? "Add anyway" : "Add & connect"}
        </button>
      </div>
    </div>
  );
}
