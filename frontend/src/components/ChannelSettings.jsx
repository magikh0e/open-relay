import { useState } from "react";
import Avatar from "./Avatar.jsx";
import ChannelWebhooks from "./ChannelWebhooks.jsx";
import { useDialog } from "../useDialog.js";

// Channel management for owners/admins: details (name, topic, privacy), member
// roles (op / transfer ownership / kick / ban), and deletion.
export default function ChannelSettings({
  channel,
  members,
  myId,
  onUpdate, // (patch) => Promise
  onSetRole, // (member, role) => void
  onKick,
  onBan,
  onDelete,
  onOpenProfile,
  onClose,
  onConfirm,}) {
  const dialogRef = useDialog(onClose);
  const [name, setName] = useState(channel.name);
  const [topic, setTopic] = useState(channel.topic || "");
  const [isPrivate, setIsPrivate] = useState(channel.kind === "private");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  // Channel key (public channels only). channel.has_password is refreshed via
  // props after onUpdate, so the section reflects the live state.
  const [pw, setPw] = useState("");
  const [pwBusy, setPwBusy] = useState(false);
  const [pwMsg, setPwMsg] = useState("");
  const [pwErr, setPwErr] = useState(false); // colours pwMsg as an error vs a notice
  const canHavePassword = channel.kind === "public" && !channel.read_only;

  async function setPassword() {
    if (pw.length < 8) return;
    setPwBusy(true);
    setPwMsg("");
    setPwErr(false);
    try {
      await onUpdate({ password: pw });
      setPw("");
      setPwMsg("Password set. New members will need it to join.");
    } catch (e) {
      setPwErr(true);
      setPwMsg(e.message);
    } finally {
      setPwBusy(false);
    }
  }

  async function removePassword() {
    setPwBusy(true);
    setPwMsg("");
    setPwErr(false);
    try {
      await onUpdate({ password: "" });
      setPwMsg("Password removed. Anyone can join now.");
    } catch (e) {
      setPwErr(true);
      setPwMsg(e.message);
    } finally {
      setPwBusy(false);
    }
  }

  const dirty =
    name !== channel.name ||
    topic !== (channel.topic || "") ||
    isPrivate !== (channel.kind === "private");

  async function save() {
    setSaving(true);
    setError("");
    try {
      await onUpdate({
        name: name.trim() || channel.name,
        topic: topic.trim(),
        is_private: isPrivate,
      });
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal settings-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Channel settings"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>Channel settings</h3>
          <button className="link" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {error && <div className="error">{error}</div>}

        <div className="settings-section">
          <label>Name</label>
          <input
            className="edit-input"
            value={name}
            maxLength={64}
            onChange={(e) => setName(e.target.value)}
          />
          <label>Topic</label>
          <input
            className="edit-input"
            value={topic}
            maxLength={512}
            placeholder="What's this channel about?"
            onChange={(e) => setTopic(e.target.value)}
          />
          <label className="check">
            <input
              type="checkbox"
              checked={isPrivate}
              onChange={(e) => setIsPrivate(e.target.checked)}
            />
            Private: hidden from the public directory (members only)
          </label>
          <button className="primary" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>

        {canHavePassword && (
          <div className="settings-section">
            <label>
              Channel password{" "}
              {channel.has_password ? (
                <span className="role-tag">🔑 protected</span>
              ) : (
                <span className="muted small">open</span>
              )}
            </label>
            <p className="muted small">
              Anyone can see this public channel, but only people with the
              password can join. Members already in stay in.
            </p>
            <input
              className="edit-input"
              type="password"
              value={pw}
              maxLength={128}
              placeholder={
                channel.has_password ? "New password" : "Set a password"
              }
              onChange={(e) => setPw(e.target.value)}
            />
            {pw.length > 0 && pw.length < 8 && (
              <div className="muted small">At least 8 characters.</div>
            )}
            <div className="settings-member-actions">
              <button
                className="mini"
                disabled={pw.length < 8 || pwBusy}
                onClick={setPassword}
              >
                {channel.has_password ? "Change password" : "Set password"}
              </button>
              {channel.has_password && (
                <button
                  className="mini ghost"
                  disabled={pwBusy}
                  onClick={removePassword}
                >
                  Remove password
                </button>
              )}
            </div>
            {pwMsg && (
              <div className={pwErr ? "error" : "muted small"}>{pwMsg}</div>
            )}
          </div>
        )}

        <div className="settings-section">
          <label>Members &amp; roles</label>
          <div className="settings-members">
            {members.map((m) => {
              const isOwner = m.role === "owner";
              const isOp = m.role === "mod";
              const targetable = m.id !== myId && !isOwner;
              return (
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
                    {isOwner && <span className="role-tag">owner</span>}
                    {isOp && <span className="role-tag op">op</span>}
                  </button>
                  {targetable && (
                    <span className="settings-member-actions">
                      <button
                        className="mini"
                        onClick={() => onSetRole(m, isOp ? "member" : "mod")}
                      >
                        {isOp ? "Remove op" : "Make op"}
                      </button>
                      <button
                        className="mini"
                        onClick={async () => {
                          const ok = await onConfirm?.({
                            title: `Transfer ownership to ${m.display_name}?`,
                            body: "You'll become an operator of this channel.",
                            confirmLabel: "Transfer ownership",
                            danger: true,
                          });
                          if (ok) onSetRole(m, "owner");
                        }}
                      >
                        Make owner
                      </button>
                      <button className="mini" onClick={() => onKick(m)}>
                        Kick
                      </button>
                      <button className="mini ghost" onClick={() => onBan(m)}>
                        Ban
                      </button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        <ChannelWebhooks channel={channel} onConfirm={onConfirm} />

        <div className="settings-danger">
          <label>Danger zone</label>
          <button className="danger-btn" onClick={onDelete}>
            Delete channel
          </button>
        </div>
      </div>
    </div>
  );
}
