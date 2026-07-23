import { useState } from "react";
import Avatar from "./Avatar.jsx";

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
}) {
  const [name, setName] = useState(channel.name);
  const [topic, setTopic] = useState(channel.topic || "");
  const [isPrivate, setIsPrivate] = useState(channel.kind === "private");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

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
      <div className="modal settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>Channel settings</h3>
          <button className="link" onClick={onClose}>
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
            Private — hidden from the public directory (members only)
          </label>
          <button className="primary" disabled={!dirty || saving} onClick={save}>
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>

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
                        onClick={() => {
                          if (
                            window.confirm(
                              `Transfer ownership to ${m.display_name}? You'll become an operator.`
                            )
                          )
                            onSetRole(m, "owner");
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
