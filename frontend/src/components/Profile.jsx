import { useEffect, useState } from "react";
import { api, tokens } from "../api.js";
import { useDialog } from "../useDialog.js";
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
  const [pwOpen, setPwOpen] = useState(false);
  const [privacyOpen, setPrivacyOpen] = useState(false);

  const isMe = user?.id === userId;
  const dialogRef = useDialog(onClose);

  useEffect(() => {
    let alive = true;
    setProfile(null);
    setEditing(false);
    setPwOpen(false);
    setPrivacyOpen(false);
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
      <div
        className="modal profile-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
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
                  <>
                    <button className="mini" onClick={() => setEditing(true)}>
                      Edit profile
                    </button>
                    <button
                      className="mini"
                      onClick={() => setPwOpen((o) => !o)}
                    >
                      {user?.has_password === false
                        ? "Set a password"
                        : "Change password"}
                    </button>
                    <button
                      className="mini"
                      onClick={() => setPrivacyOpen((o) => !o)}
                    >
                      Privacy
                    </button>
                  </>
                )}
              </div>
            )}

            {isMe && pwOpen && <PasswordForm hasPassword={user?.has_password !== false} />}
            {isMe && privacyOpen && <PrivacyForm />}

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

// Change (or first-time set) your own password. SSO accounts have no existing
// password to confirm, so the current-password field is skipped for them.
function PasswordForm({ hasPassword }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(e) {
    e.preventDefault();
    setError("");
    if (next !== confirm) {
      setError("Those passwords don't match.");
      return;
    }
    if (next.length < 8) {
      setError("Use at least 8 characters.");
      return;
    }
    setBusy(true);
    try {
      // Changing the password revokes every existing token, so the response
      // carries a fresh pair — store it or we'd sign ourselves out.
      const pair = await api("/users/me/password", {
        method: "POST",
        body: {
          current_password: hasPassword ? current : null,
          new_password: next,
        },
      });
      if (pair?.access_token) tokens.set(pair);
      setDone(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="pw-form">
        <div className="pw-ok">
          ✓ Password updated. Every other device has been signed out.
        </div>
      </div>
    );
  }

  return (
    <form className="pw-form" onSubmit={submit}>
      {hasPassword && (
        <input
          type="password"
          placeholder="Current password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
        />
      )}
      <input
        type="password"
        placeholder="New password"
        autoComplete="new-password"
        value={next}
        onChange={(e) => setNext(e.target.value)}
      />
      <input
        type="password"
        placeholder="Confirm new password"
        autoComplete="new-password"
        value={confirm}
        onChange={(e) => setConfirm(e.target.value)}
      />
      {error && <div className="error">{error}</div>}
      <button
        className="primary"
        disabled={busy || !next || (hasPassword && !current)}
      >
        {busy ? "Saving…" : hasPassword ? "Change password" : "Set password"}
      </button>
      <div className="muted small">
        This is separate from your message-encryption passphrase — changing it
        won't affect encrypted DMs.
      </div>
    </form>
  );
}

// Privacy preferences. Each toggle is enforced on the server too, so turning
// one off actually stops the signal being produced.
const PRIVACY_OPTIONS = [
  {
    key: "share_typing",
    label: "Show when I'm typing",
    hint: "Others see “is typing…” while you write.",
  },
  {
    key: "share_presence",
    label: "Show when I'm online",
    hint: "Turn off to always appear offline.",
  },
  {
    key: "allow_dms",
    label: "Allow new direct messages",
    hint: "Existing conversations keep working either way.",
  },
  {
    key: "discoverable",
    label: "Let people find me in search",
    hint: "You stay visible in channels you're already in.",
  },
];

function PrivacyForm() {
  const { user, updateUser } = useAuth();
  const [saving, setSaving] = useState("");
  const [error, setError] = useState("");

  async function toggle(key, value) {
    setSaving(key);
    setError("");
    // Optimistic: the switch should feel instant.
    updateUser({ [key]: value });
    try {
      await api("/users/me/settings", { method: "PATCH", body: { [key]: value } });
    } catch (e) {
      updateUser({ [key]: !value }); // put it back
      setError(e.message);
    } finally {
      setSaving("");
    }
  }

  return (
    <div className="privacy-form">
      {PRIVACY_OPTIONS.map((opt) => {
        const on = user?.[opt.key] !== false;
        return (
          <label key={opt.key} className="privacy-row">
            <input
              type="checkbox"
              checked={on}
              disabled={saving === opt.key}
              onChange={(e) => toggle(opt.key, e.target.checked)}
            />
            <span className="privacy-text">
              <span className="privacy-label">{opt.label}</span>
              <span className="muted small">{opt.hint}</span>
            </span>
          </label>
        );
      })}
      {error && <div className="error">{error}</div>}
      <div className="muted small">
        These are enforced by the server, not just hidden here.
      </div>
    </div>
  );
}
