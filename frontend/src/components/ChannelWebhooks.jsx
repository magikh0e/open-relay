import { useEffect, useState } from "react";
import { api } from "../api.js";

// Manage a channel's incoming webhooks (owner/mod only). The secret URL is
// shown once, right after creation; after that only the name remains.
export default function ChannelWebhooks({ channel, onConfirm }) {
  const [hooks, setHooks] = useState(null); // null while loading
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [freshUrl, setFreshUrl] = useState(""); // one-time URL after create
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let alive = true;
    api(`/channels/${channel.id}/webhooks`)
      .then((rows) => alive && setHooks(rows || []))
      .catch(() => alive && setHooks([]));
    return () => {
      alive = false;
    };
  }, [channel.id]);

  async function create(e) {
    e.preventDefault();
    const n = name.trim();
    if (!n) return;
    setBusy(true);
    setError("");
    setFreshUrl("");
    try {
      const wh = await api(`/channels/${channel.id}/webhooks`, {
        method: "POST",
        body: { name: n },
      });
      setHooks((h) => [
        ...(h || []),
        { id: wh.id, name: wh.name, created_at: wh.created_at },
      ]);
      setFreshUrl(wh.url);
      setCopied(false);
      setName("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(wh) {
    const ok = await onConfirm?.({
      title: `Delete the "${wh.name}" webhook?`,
      body: "Its URL stops working immediately.",
      confirmLabel: "Delete webhook",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/channels/${channel.id}/webhooks/${wh.id}`, { method: "DELETE" });
      setHooks((h) => h.filter((x) => x.id !== wh.id));
    } catch (err) {
      setError(err.message);
    }
  }

  function copy() {
    navigator.clipboard
      ?.writeText(freshUrl)
      .then(() => setCopied(true))
      .catch(() => {});
  }

  return (
    <div className="settings-section">
      <label>Webhooks</label>
      <p className="settings-hint">
        Post into this channel from outside (CI, alerts, home automation) by
        sending JSON to a secret URL.
      </p>

      {freshUrl && (
        <div className="webhook-fresh">
          <div className="webhook-fresh-note">
            Copy this URL now. For security it is not shown again.
          </div>
          <div className="webhook-fresh-row">
            <code className="webhook-url">{freshUrl}</code>
            <button className="mini" type="button" onClick={copy}>
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        </div>
      )}

      {hooks === null ? (
        <div className="muted small">Loading…</div>
      ) : hooks.length === 0 ? (
        <div className="muted small">No webhooks yet.</div>
      ) : (
        <div className="webhook-list">
          {hooks.map((wh) => (
            <div key={wh.id} className="webhook-row">
              <span className="webhook-name">{wh.name}</span>
              <button
                className="mini ghost"
                type="button"
                onClick={() => revoke(wh)}
              >
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}

      <form className="webhook-create" onSubmit={create}>
        <input
          className="edit-input"
          placeholder="New webhook name (e.g. CI)"
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
        />
        <button className="primary" disabled={busy || !name.trim()}>
          {busy ? "Creating…" : "Create"}
        </button>
      </form>
      {error && <div className="error">{error}</div>}
    </div>
  );
}
