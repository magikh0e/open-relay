import { useState } from "react";
import { api } from "../api.js";
import Avatar from "./Avatar.jsx";

// Group DM management: rename (owner), see/add/remove members, and leave.
// Members are managed here rather than via channel moderation (no roles, kicks
// or bans), and the group is plaintext, so there's no privacy/password section.
export default function GroupInfo({
  group,
  members,
  myId,
  isOwner,
  onRename, // (name) => Promise
  onAddMember, // (userId) => Promise
  onRemoveMember, // (userId) => Promise
  onLeave,
  onOpenProfile,
  onClose,
}) {
  const [name, setName] = useState(group.name);
  const [savingName, setSavingName] = useState(false);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);

  const memberIds = new Set(members.map((m) => m.id));

  async function saveName() {
    const trimmed = name.trim();
    if (!trimmed || trimmed === group.name) return;
    setSavingName(true);
    setError("");
    try {
      await onRename(trimmed);
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingName(false);
    }
  }

  async function search(value) {
    setQ(value);
    if (value.trim().length < 2) return setResults([]);
    try {
      const rows = await api(`/users/search?q=${encodeURIComponent(value)}`);
      setResults(rows.filter((u) => !memberIds.has(u.id)));
    } catch {
      setResults([]);
    }
  }

  async function add(userId) {
    setBusy(true);
    setError("");
    try {
      await onAddMember(userId);
      setQ("");
      setResults([]);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(userId) {
    setError("");
    try {
      await onRemoveMember(userId);
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>👥 Group</h3>
          <button className="link" onClick={onClose}>
            ✕
          </button>
        </div>
        {error && <div className="error">{error}</div>}

        <div className="settings-section">
          <label>Name</label>
          {isOwner ? (
            <div className="group-name-row">
              <input
                className="edit-input"
                value={name}
                maxLength={64}
                onChange={(e) => setName(e.target.value)}
              />
              <button
                className="mini"
                disabled={
                  savingName || !name.trim() || name.trim() === group.name
                }
                onClick={saveName}
              >
                {savingName ? "Saving…" : "Rename"}
              </button>
            </div>
          ) : (
            <div className="profile-value">{group.name}</div>
          )}
        </div>

        <div className="settings-section">
          <label>Members ({members.length})</label>
          {isOwner && (
            <div className="group-add">
              <input
                placeholder="Add someone…"
                value={q}
                onChange={(e) => search(e.target.value)}
              />
              {results.length > 0 && (
                <div className="results">
                  {results.map((u) => (
                    <button
                      key={u.id}
                      className="result"
                      disabled={busy}
                      onClick={() => add(u.id)}
                    >
                      <Avatar name={u.display_name} admin={u.is_admin} />
                      <span>
                        {u.display_name}{" "}
                        <span className="muted">@{u.username}</span>
                      </span>
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          <div className="settings-members">
            {members.map((m) => (
              <div key={m.id} className="settings-member">
                <button
                  className="settings-member-main"
                  onClick={() => onOpenProfile?.(m.id)}
                  title={`@${m.username}`}
                >
                  <Avatar name={m.display_name} admin={m.is_admin} />
                  <span className="member-name">{m.display_name}</span>
                  {m.role === "owner" && (
                    <span className="role-tag">owner</span>
                  )}
                </button>
                {isOwner && m.id !== myId && m.role !== "owner" && (
                  <span className="settings-member-actions">
                    <button className="mini ghost" onClick={() => remove(m.id)}>
                      Remove
                    </button>
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="settings-danger">
          <label>Leave</label>
          <button className="danger-btn" onClick={onLeave}>
            Leave group
          </button>
        </div>
      </div>
    </div>
  );
}
