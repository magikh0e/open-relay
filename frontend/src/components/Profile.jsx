import { useEffect, useState } from "react";
import { api, tokens } from "../api.js";
import { disablePush, enablePush, isSubscribed, pushSupported } from "../push.js";
import { useDialog } from "../useDialog.js";
import { useAuth } from "../auth.jsx";
import Avatar from "./Avatar.jsx";

// Compact relative time for "last active" ("just now", "4m ago", a date once
// it's older than a month).
function timeAgo(iso) {
  const secs = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000));
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return new Date(iso).toLocaleDateString();
}

// View a user's profile; if it's you, edit it. All fields render as text
// (React-escaped) — never innerHTML.
export default function Profile({ userId, onClose, onMessage }) {
  const { user, updateUser } = useAuth();
  const [profile, setProfile] = useState(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState({ display_name: "", pronouns: "", bio: "" });
  const [saving, setSaving] = useState(false);
  // One panel at a time. They were five independent booleans, which let all of
  // them open at once and stacked into a modal taller than any screen.
  const [panel, setPanel] = useState(null);
  const toggle = (name) => setPanel((p) => (p === name ? null : name));

  const isMe = user?.id === userId;
  const dialogRef = useDialog(onClose);

  useEffect(() => {
    let alive = true;
    setProfile(null);
    setEditing(false);
    setPanel(null);
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
                  {profile.pronouns || (
                    <span className="muted">Not set</span>
                  )}
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

            {profile.last_active_at && (
              <div className="profile-field">
                <label>Last active</label>
                <div className="profile-value muted">
                  {timeAgo(profile.last_active_at)}
                </div>
              </div>
            )}

            <div className="profile-field">
              <label>Registration</label>
              <div className="profile-value muted">
                {profile.registered_via_invite ? (
                  <>
                    Invited by @
                    {profile.invited_by_username || "a former admin"}
                  </>
                ) : (
                  "Open registration"
                )}
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
                    <PanelButton
                      name="password"
                      panel={panel}
                      onToggle={toggle}
                      label={
                        user?.has_password === false
                          ? "Set a password"
                          : "Change password"
                      }
                    />
                    <PanelButton
                      name="privacy"
                      panel={panel}
                      onToggle={toggle}
                      label="Privacy"
                    />
                    <PanelButton
                      name="data"
                      panel={panel}
                      onToggle={toggle}
                      label="Your data"
                    />
                  </>
                )}
              </div>
            )}

            {/* Admin tools are set apart: they manage the server, not you, and
                mixing them in made six equal-looking buttons with no hierarchy. */}
            {isMe && user?.is_admin && !editing && (
              <div className="profile-admin">
                <div className="profile-admin-label">Server admin</div>
                <div className="profile-actions">
                  <PanelButton
                    name="invites"
                    panel={panel}
                    onToggle={toggle}
                    label="Invites"
                  />
                  <PanelButton
                    name="bots"
                    panel={panel}
                    onToggle={toggle}
                    label="Bots"
                  />
                </div>
              </div>
            )}

            {isMe && panel === "password" && (
              <PasswordForm hasPassword={user?.has_password !== false} />
            )}
            {isMe && panel === "data" && <AccountData onClose={onClose} />}
            {isMe && user?.is_admin && panel === "invites" && <InvitesAdmin />}
            {isMe && user?.is_admin && panel === "bots" && <BotsAdmin />}
            {isMe && panel === "privacy" && (
              <>
                <PrivacyForm />
                <NotificationToggle />
              </>
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

// A toggle for one of the profile's disclosure panels. It reports its state
// through aria-expanded and a pressed look, so which panel is open is visible
// rather than something you infer from what appeared below.
function PanelButton({ name, panel, onToggle, label }) {
  const open = panel === name;
  return (
    <button
      className={"mini" + (open ? " active" : "")}
      aria-expanded={open}
      onClick={() => onToggle(name)}
    >
      {label}
    </button>
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
        This is separate from your message-encryption passphrase; changing it
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
  {
    key: "share_last_active",
    label: "Show when I was last active",
    hint: "Off hides it from others; you and admins still see it, and it's still recorded.",
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

// Browser push notifications. Kept beside the privacy toggles because it's the
// same kind of decision: what this device is allowed to do on your behalf.
function NotificationToggle() {
  const [on, setOn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const supported = pushSupported();

  useEffect(() => {
    let alive = true;
    isSubscribed().then((v) => alive && setOn(v));
    return () => {
      alive = false;
    };
  }, []);

  async function toggle(next) {
    setBusy(true);
    setError("");
    try {
      if (next) await enablePush();
      else await disablePush();
      setOn(next);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  if (!supported) return null;

  return (
    <div className="privacy-form">
      <label className="privacy-row">
        <input
          type="checkbox"
          checked={on}
          disabled={busy}
          onChange={(e) => toggle(e.target.checked)}
        />
        <span className="privacy-text">
          <span className="privacy-label">Notify me on this device</span>
          <span className="muted small">
            For direct messages and mentions. Notifications say who and where,
            never what was said.
          </span>
        </span>
      </label>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

// Export or delete your own account. Deletion is irreversible, so it demands
// the password and a typed confirmation rather than a single click.
function AccountData({ onClose }) {
  const { logout } = useAuth();
  const [confirm, setConfirm] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  async function download() {
    setBusy("export");
    setError("");
    try {
      const data = await api("/users/me/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "open-relay-export.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy("");
    }
  }

  async function remove() {
    setBusy("delete");
    setError("");
    try {
      await api("/users/me/delete", { method: "POST", body: { password } });
      onClose?.();
      logout();
    } catch (e) {
      setError(e.message);
      setBusy("");
    }
  }

  return (
    <div className="privacy-form">
      <div>
        <button className="mini" disabled={busy === "export"} onClick={download}>
          {busy === "export" ? "Preparing…" : "Download my data"}
        </button>
        <div className="muted small" style={{ marginTop: 6 }}>
          Your profile, settings and messages as JSON. Encrypted messages are
          included as the scrambled text the server holds; it can't read them
          either.
        </div>
      </div>

      <div className="danger-zone">
        <div className="privacy-label">Delete my account</div>
        <div className="muted small">
          Permanent and immediate. Your account, encryption keys and settings
          are erased. Messages you sent stay in other people's conversations but
          are no longer attributed to you.
        </div>
        <input
          type="password"
          placeholder="Your password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        <input
          placeholder='Type DELETE to confirm'
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
        />
        <button
          className="primary danger-btn"
          disabled={confirm !== "DELETE" || busy === "delete"}
          onClick={remove}
        >
          {busy === "delete" ? "Deleting…" : "Delete my account"}
        </button>
      </div>
      {error && <div className="error">{error}</div>}
    </div>
  );
}

// Admin-only: mint, copy and revoke single-use invite codes. Only shown to
// site admins on their own profile. The endpoints are require_admin-gated
// server-side, so a non-admin can't reach these even if the UI leaked.
function InvitesAdmin() {
  const [invites, setInvites] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(null);

  useEffect(() => {
    let alive = true;
    api("/invites")
      .then((rows) => alive && setInvites(rows))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  function copy(code) {
    navigator.clipboard?.writeText(code);
    setCopied(code);
    setTimeout(() => setCopied((c) => (c === code ? null : c)), 1500);
  }

  async function generate() {
    setBusy(true);
    setError("");
    try {
      const inv = await api("/invites", { method: "POST" });
      setInvites((prev) => [inv, ...(prev || [])]);
      copy(inv.code); // hand the fresh code straight to the clipboard
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(id) {
    setError("");
    try {
      await api(`/invites/${id}`, { method: "DELETE" });
      setInvites((prev) => prev.filter((i) => i.id !== id));
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="privacy-form invites-admin">
      <div className="invites-head">
        <div className="privacy-label">Invite codes</div>
        <button className="mini" disabled={busy} onClick={generate}>
          {busy ? "Generating…" : "Generate code"}
        </button>
      </div>
      <div className="muted small">
        When the server is invite-only, new accounts need one of these. Each
        code works once; revoke any you haven't handed out.
      </div>
      {error && <div className="error">{error}</div>}
      {invites === null ? (
        <div className="muted small">Loading…</div>
      ) : invites.length === 0 ? (
        <div className="muted small">No codes yet.</div>
      ) : (
        <ul className="invites-list">
          {invites.map((inv) => (
            <li key={inv.id} className="invite-row">
              <div className="invite-main">
                <code className="invite-code">{inv.code}</code>
                <span className={`invite-status${inv.used_at ? " used" : ""}`}>
                  {inv.used_at ? "used" : "unused"}
                </span>
                {!inv.used_at && (
                  <span className="invite-actions">
                    <button className="mini" onClick={() => copy(inv.code)}>
                      {copied === inv.code ? "Copied" : "Copy"}
                    </button>
                    <button
                      className="mini ghost"
                      onClick={() => revoke(inv.id)}
                    >
                      Revoke
                    </button>
                  </span>
                )}
              </div>
              <div className="invite-meta muted small">
                Created by @{inv.created_by_username || "deleted user"}
                {inv.used_at && (
                  <>
                    {" · used by @"}
                    {inv.used_by_username || "deleted user"}
                  </>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Admin-only: mint bot accounts, hand out their token once, rotate or remove
// them. A bot's token is a bearer credential with no expiry, so the only
// honest moment to show it is the moment it is created.
function BotsAdmin() {
  const [bots, setBots] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [scopes, setScopes] = useState(["read", "write"]);
  // The one-time token, held only until the admin dismisses it.
  const [fresh, setFresh] = useState(null);
  const [copied, setCopied] = useState(false);
  // Both actions here are destructive in a way a single click should not
  // trigger: rotating cuts off a bot that is currently running, and
  // deleting is permanent. Holds {id, action} while awaiting confirmation.
  const [confirming, setConfirming] = useState(null);

  useEffect(() => {
    let alive = true;
    api("/bots")
      .then((rows) => alive && setBots(rows))
      .catch((e) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, []);

  function toggleScope(s) {
    setScopes((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    );
  }

  async function copy(token) {
    try {
      await navigator.clipboard.writeText(token);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Couldn't copy. Select the token and copy it by hand.");
    }
  }

  async function create() {
    setBusy(true);
    setError("");
    try {
      const bot = await api("/bots", {
        method: "POST",
        body: {
          username: username.trim(),
          display_name: displayName.trim() || null,
          scopes,
        },
      });
      setBots((prev) => [bot, ...(prev || [])]);
      setFresh(bot);
      setCreating(false);
      setUsername("");
      setDisplayName("");
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function rotate(bot) {
    setError("");
    try {
      const updated = await api(`/bots/${bot.id}/token`, { method: "POST" });
      setBots((prev) => prev.map((b) => (b.id === bot.id ? updated : b)));
      setFresh(updated);
    } catch (e) {
      setError(e.message);
    }
  }

  async function remove(bot) {
    setError("");
    try {
      await api(`/bots/${bot.id}`, { method: "DELETE" });
      setBots((prev) => prev.filter((b) => b.id !== bot.id));
    } catch (e) {
      setError(e.message);
    }
  }

  return (
    <div className="privacy-form invites-admin">
      <div className="invites-head">
        <div className="privacy-label">Bot accounts</div>
        <button className="mini" onClick={() => setCreating((c) => !c)}>
          {creating ? "Cancel" : "New bot"}
        </button>
      </div>
      <div className="muted small">
        A bot is a program that can read and post in the channels you add it
        to, and nothing else. It cannot join on its own, open DMs, or moderate.
        It has no encryption key, so a group with a bot in it cannot be
        encrypted.
      </div>
      {error && <div className="error">{error}</div>}

      {fresh && (
        <div className="bot-token-reveal">
          <div className="privacy-label">Token for @{fresh.username}</div>
          <code className="bot-token">{fresh.token}</code>
          <div className="invite-actions">
            <button className="mini" onClick={() => copy(fresh.token)}>
              {copied ? "Copied" : "Copy"}
            </button>
            <button className="mini ghost" onClick={() => setFresh(null)}>
              Done
            </button>
          </div>
          <div className="muted small">
            Copy it now. Only a hash is stored, so this cannot be shown again;
            if it is lost, rotate the token to get a new one.
          </div>
        </div>
      )}

      {creating && (
        <div className="bot-create">
          <input
            className="edit-input"
            placeholder="Username, e.g. ci-bot"
            value={username}
            maxLength={32}
            onChange={(e) => setUsername(e.target.value)}
          />
          <input
            className="edit-input"
            placeholder="Display name (optional)"
            value={displayName}
            maxLength={64}
            onChange={(e) => setDisplayName(e.target.value)}
          />
          <div className="bot-scopes">
            {[
              ["read", "Read channels it is in"],
              ["write", "Post messages"],
              ["react", "Add reactions"],
            ].map(([s, label]) => (
              <label key={s} className="check">
                <input
                  type="checkbox"
                  checked={scopes.includes(s)}
                  onChange={() => toggleScope(s)}
                />
                {label}
              </label>
            ))}
          </div>
          <button
            className="primary"
            disabled={busy || username.trim().length < 2}
            onClick={create}
          >
            {busy ? "Creating…" : "Create bot"}
          </button>
        </div>
      )}

      {bots === null ? (
        <div className="muted small">Loading…</div>
      ) : bots.length === 0 ? (
        <div className="muted small">No bots yet.</div>
      ) : (
        <ul className="invites-list">
          {bots.map((b) => (
            <li key={b.id} className="invite-row">
              <div className="invite-main">
                <code className="invite-code">@{b.username}</code>
                <span className="role-tag bot">BOT</span>
                <span className="invite-actions">
                  {confirming?.id === b.id ? (
                    <>
                      <button
                        className="mini danger-btn"
                        onClick={() => {
                          const act = confirming.action;
                          setConfirming(null);
                          act === "rotate" ? rotate(b) : remove(b);
                        }}
                      >
                        {confirming.action === "rotate"
                          ? "Rotate, cutting it off"
                          : "Delete permanently"}
                      </button>
                      <button
                        className="mini ghost"
                        onClick={() => setConfirming(null)}
                      >
                        Cancel
                      </button>
                    </>
                  ) : (
                    <>
                      <button
                        className="mini"
                        onClick={() =>
                          setConfirming({ id: b.id, action: "rotate" })
                        }
                      >
                        Rotate token
                      </button>
                      <button
                        className="mini ghost"
                        onClick={() =>
                          setConfirming({ id: b.id, action: "delete" })
                        }
                      >
                        Delete
                      </button>
                    </>
                  )}
                </span>
              </div>
              <div className="invite-meta muted small">
                {b.scopes.length ? b.scopes.join(", ") : "no scopes"}
                {" · "}
                {b.last_used_at
                  ? `last used ${timeAgo(b.last_used_at)}`
                  : "never used"}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
