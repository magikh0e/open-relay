import { useState } from "react";
import { api } from "../api.js";
import Avatar from "./Avatar.jsx";
import { useDialog } from "../useDialog.js";

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
  onConfirm, // (opts) => Promise<bool>, the app's in-house confirm dialog
  encrypted = false,
  canEncrypt = false, // our own key is unlocked, so we can seal shares
  onEnableEncryption, // () => Promise, publishes the first key epoch
  onClose,
}) {
  const dialogRef = useDialog(onClose);
  const [name, setName] = useState(group.name);
  const [savingName, setSavingName] = useState(false);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [busy, setBusy] = useState(false);
  const [removingId, setRemovingId] = useState(null);
  const [enabling, setEnabling] = useState(false);

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

  async function enable() {
    setEnabling(true);
    setError("");
    try {
      await onEnableEncryption();
    } catch (e) {
      setError(e.message);
    } finally {
      setEnabling(false);
    }
  }

  async function remove(member) {
    if (removingId) return; // guard against a double-click firing twice
    const ok = onConfirm
      ? await onConfirm({
          title: `Remove ${member.display_name}?`,
          body: "They'll lose access to this group and need to be added back to rejoin.",
          confirmLabel: "Remove",
          danger: true,
        })
      : true;
    if (!ok) return;
    setRemovingId(member.id);
    setError("");
    try {
      await onRemoveMember(member.id);
    } catch (e) {
      setError(e.message);
    } finally {
      setRemovingId(null);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal settings-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Group info"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Group info</h3>
          <button className="link" onClick={onClose} aria-label="Close">
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
                  {m.is_bot && (
                    <span className="role-tag bot" title="A program, not a person">
                      BOT
                    </span>
                  )}
                  {m.role === "owner" && (
                    <span className="role-tag">owner</span>
                  )}
                </button>
                {isOwner && m.id !== myId && m.role !== "owner" && (
                  <span className="settings-member-actions">
                    <button
                      className="mini ghost"
                      disabled={removingId === m.id}
                      onClick={() => remove(m)}
                    >
                      {removingId === m.id ? "Removing…" : "Remove"}
                    </button>
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>

        <div className="settings-section">
          <label>
            Encryption{" "}
            {encrypted ? (
              <span className="role-tag">🔒 on</span>
            ) : (
              <span className="muted small">off</span>
            )}
          </label>
          {encrypted ? (
            <p className="muted small">
              Messages and files here are encrypted in your browser; the server
              stores only ciphertext it cannot read. The key changes whenever
              someone joins or leaves, so a new member sees what is said from
              their arrival onward, not the history before it.
            </p>
          ) : (
            <>
              <p className="muted small">
                This group is plaintext: whoever runs the server can read it,
                the same as a channel. Turning encryption on applies from here
                onward, and messages already sent stay as they are.
              </p>
              {isOwner && (
                <>
                  <button
                    className="mini"
                    disabled={enabling || !canEncrypt}
                    onClick={enable}
                  >
                    {enabling ? "Turning on…" : "Turn on encryption"}
                  </button>
                  <p className="muted small">
                    {canEncrypt
                      ? "Everyone in the group needs encryption set up before this can be switched on."
                      : "Unlock your own encryption key first, from your profile."}
                  </p>
                </>
              )}
            </>
          )}
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
