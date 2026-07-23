import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import Avatar from "./Avatar.jsx";

// View a user's profile; if it's you, edit it. All fields render as text
// (React-escaped) — never innerHTML.
export default function Profile({ userId, onClose, onMessage }) {
  const { user, updateUser } = useAuth();
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ display_name: "", pronouns: "", bio: "" });
  const [saving, setSaving] = useState(false);

  const isMe = user?.id === userId;

  useEffect(() => {
    let alive = true;
    setProfile(null);
    setEditing(false);
    setError("");
    api(`/users/${userId}`)
      .then((p) => {
        if (!alive) return;
        setProfile(p);
        setForm({
          display_name: p.display_name,
          pronouns: p.pronouns,
          bio: p.bio,
        });
      })
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [userId]);

  async function save() {
    setSaving(true);
    setError("");
    try {
      const updated = await api("/users/me", { method: "PATCH", body: form });
      setProfile(updated);
      updateUser({ display_name: updated.display_name });
      setEditing(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal profile-modal" onClick={(e) => e.stopPropagation()}>
        <button className="link profile-close" onClick={onClose}>
          ✕
        </button>

        {error && <div className="error">{error}</div>}
        {!profile && !error && <div className="muted">Loading…</div>}

        {profile && (
          <>
            <div className="profile-head">
              <Avatar
                name={profile.display_name}
                admin={profile.is_admin}
                size="profile-avatar"
              />
              <div>
                {editing ? (
                  <input
                    className="edit-input"
                    value={form.display_name}
                    maxLength={64}
                    onChange={(e) =>
                      setForm({ ...form, display_name: e.target.value })
                    }
                  />
                ) : (
                  <div className="profile-name">{profile.display_name}</div>
                )}
                <div className="muted small">@{profile.username}</div>
              </div>
            </div>

            <div className="profile-field">
              <label>Pronouns</label>
              {editing ? (
                <input
                  className="edit-input"
                  value={form.pronouns}
                  maxLength={40}
                  placeholder="e.g. they/them"
                  onChange={(e) =>
                    setForm({ ...form, pronouns: e.target.value })
                  }
                />
              ) : (
                <div className="profile-value">
                  {profile.pronouns || <span className="muted">—</span>}
                </div>
              )}
            </div>

            <div className="profile-field">
              <label>Bio</label>
              {editing ? (
                <textarea
                  className="edit-input"
                  rows={4}
                  value={form.bio}
                  maxLength={500}
                  placeholder="Tell people about yourself…"
                  onChange={(e) => setForm({ ...form, bio: e.target.value })}
                />
              ) : (
                <div className="profile-value profile-bio">
                  {profile.bio || <span className="muted">No bio yet</span>}
                </div>
              )}
            </div>

            <div className="profile-field">
              <label>Joined</label>
              <div className="profile-value muted">
                {new Date(profile.created_at).toLocaleDateString()}
              </div>
            </div>

            {isMe && (
              <div className="profile-actions">
                {editing ? (
                  <>
                    <button
                      className="primary"
                      disabled={saving || !form.display_name.trim()}
                      onClick={save}
                    >
                      {saving ? "Saving…" : "Save"}
                    </button>
                    <button
                      className="mini ghost"
                      onClick={() => {
                        setEditing(false);
                        setForm({
                          display_name: profile.display_name,
                          pronouns: profile.pronouns,
                          bio: profile.bio,
                        });
                      }}
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button className="mini" onClick={() => setEditing(true)}>
                    Edit profile
                  </button>
                )}
              </div>
            )}

            {!isMe && (
              <div className="profile-actions">
                <button
                  className="primary"
                  onClick={() => onMessage?.(profile.id)}
                >
                  💬 Message
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
