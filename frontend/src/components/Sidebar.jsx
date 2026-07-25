import { useState } from "react";
import { api } from "../api.js";
import { serverLabel } from "../config.js";
import { useAuth } from "../auth.jsx";
import { useDialog } from "../useDialog.js";
import Avatar from "./Avatar.jsx";
import ServerPicker from "./ServerPicker.jsx";

export default function Sidebar({
  channels,
  dms,
  activeId,
  online,
  onOpen,
  onJoin,
  onRefresh,
  onOpenProfile,
  onOpenSearch,
  onCloseDm,
  onLeaveGroup,
  onCreateGroup,
}) {
  const { user, logout } = useAuth();
  const [creating, setCreating] = useState(false);
  const [dmSearch, setDmSearch] = useState(false);
  const [joinPw, setJoinPw] = useState(null); // channel awaiting a password to join
  const [serverOpen, setServerOpen] = useState(false);

  const joined = channels.filter((c) => c.is_member);
  const discover = channels.filter((c) => !c.is_member);

  return (
    <aside className="sidebar">
      <div className="me">
        <button
          className="me-btn"
          onClick={() => onOpenProfile(user.id)}
          title="View / edit your profile"
        >
          <Avatar name={user.display_name} admin={user.is_admin} size="" />
          <div className="me-name">{user.display_name}</div>
        </button>
        <button className="link" onClick={logout} title="Log out" aria-label="Log out">
          ⏻
        </button>
      </div>

      <button className="search-trigger" onClick={onOpenSearch}>
        <span>🔍</span> Search messages
      </button>

      <div className="section">
        <div className="section-head">
          <span>Channels</span>
          <button
            className="link"
            onClick={() => setCreating(true)}
            title="Create a channel"
            aria-label="Create a channel"
          >
            +
          </button>
        </div>
        {joined.map((c) => (
          <button
            key={c.id}
            className={`row ${c.id === activeId ? "active" : ""}`}
            onClick={() => onOpen(c.id)}
          >
            <span className="hash">
              {c.read_only
                ? "📣"
                : c.kind === "private"
                ? "🔒"
                : c.has_password
                ? "🔑"
                : "#"}
            </span>
            <span className="row-name">{c.name}</span>
            {(c.mention_count > 0 || c.unread_count > 0) && (
                <span className={`unread-badge ${c.mention_count > 0 ? "mention" : ""}`}>
                  {c.mention_count > 0 ? `@${c.mention_count}` : c.unread_count}
                </span>
              )}
          </button>
        ))}
      </div>

      {discover.length > 0 && (
        <div className="section">
          <div className="section-head">
            <span>Discover</span>
          </div>
          {discover.map((c) => (
            <div key={c.id} className="row discover">
              <span className="hash">{c.has_password ? "🔑" : "#"}</span>
              <span className="row-name">{c.name}</span>
              <button
                className="mini"
                onClick={() => (c.has_password ? setJoinPw(c) : onJoin(c))}
              >
                Join
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <span>Direct Messages</span>
          <button
            className="link"
            onClick={() => setDmSearch(true)}
            title="Start a conversation"
            aria-label="Start a conversation"
          >
            +
          </button>
        </div>
        {dms.map((c) => {
          // For a 1:1 DM, name is the other person's display name; a group
          // carries its own name and gets a group glyph.
          const isGroup = c.kind === "group";
          return (
            <div key={c.id} className="dm-row-wrap">
              <button
                className={`row ${c.id === activeId ? "active" : ""}`}
                onClick={() => onOpen(c.id)}
              >
                <span className="dot-name">
                  {isGroup && <span className="hash">👥</span>}
                  <span className="row-name">{c.name}</span>
                </span>
                {(c.mention_count > 0 || c.unread_count > 0) && (
                <span className={`unread-badge ${c.mention_count > 0 ? "mention" : ""}`}>
                  {c.mention_count > 0 ? `@${c.mention_count}` : c.unread_count}
                </span>
              )}
              </button>
              {isGroup
                ? onLeaveGroup && (
                    <button
                      className="dm-close"
                      title="Leave this group"
                      onClick={(e) => {
                        e.stopPropagation();
                        onLeaveGroup(c);
                      }}
                      aria-label="Leave this group"
                    >
                      ✕
                    </button>
                  )
                : onCloseDm && (
                    <button
                      className="dm-close"
                      title="Close this conversation (hides it for you only)"
                      onClick={(e) => {
                        e.stopPropagation();
                        onCloseDm(c);
                      }}
                      aria-label="Close this conversation (hides it for you only)"
                    >
                      ✕
                    </button>
                  )}
            </div>
          );
        })}
      </div>

      <button
        className="server-switch"
        onClick={() => setServerOpen(true)}
        title="Switch server or add another"
      >
        <span className="server-switch-icon">⇄</span>
        <span className="server-switch-label">
          {serverLabel().replace(/^https?:\/\//, "")}
        </span>
      </button>

      {serverOpen && <ServerPicker onClose={() => setServerOpen(false)} />}
      {creating && (
        <CreateChannel
          onClose={() => setCreating(false)}
          onCreated={async (id) => {
            setCreating(false);
            await onRefresh();
            onOpen(id);
          }}
        />
      )}
      {dmSearch && (
        <NewDM
          onClose={() => setDmSearch(false)}
          onCreateGroup={onCreateGroup}
          onOpened={async (id) => {
            setDmSearch(false);
            await onRefresh();
            onOpen(id);
          }}
        />
      )}
      {joinPw && (
        <JoinPassword
          channel={joinPw}
          onClose={() => setJoinPw(null)}
          onJoin={async (password) => {
            await onJoin(joinPw, password);
            setJoinPw(null);
          }}
        />
      )}
    </aside>
  );
}

function CreateChannel({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");

  // A channel key only applies to public channels; too-short keys are blocked.
  const pwTooShort = !isPrivate && password.length > 0 && password.length < 8;

  async function create() {
    setBusy(true);
    setError("");
    try {
      const body = { slug, name, topic: "", is_private: isPrivate };
      if (!isPrivate && password) body.password = password;
      const ch = await api("/channels", { method: "POST", body });
      onCreated(ch.id);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Modal title="Create channel" onClose={onClose}>
      <input
        placeholder="Channel name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        autoFocus
      />
      {slug && <div className="muted small">#{slug}</div>}
      <label className="check">
        <input
          type="checkbox"
          checked={isPrivate}
          onChange={(e) => setIsPrivate(e.target.checked)}
        />
        Private (invite-only)
      </label>
      {!isPrivate && (
        <>
          <input
            type="password"
            placeholder="Password (optional)"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <div className="muted small">
            {pwTooShort
              ? "At least 8 characters."
              : "Leave blank for an open channel; anyone needs this key to join."}
          </div>
        </>
      )}
      {error && <div className="error">{error}</div>}
      <button
        className="primary"
        disabled={slug.length < 2 || pwTooShort || busy}
        onClick={create}
      >
        {busy ? "Creating…" : "Create"}
      </button>
    </Modal>
  );
}

// Prompt for a channel key when joining a password-protected channel. Stays
// open on a wrong key so the user can retry.
function JoinPassword({ channel, onClose, onJoin }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!password) return;
    setBusy(true);
    setError("");
    try {
      await onJoin(password);
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Modal title={`Join #${channel.name}`} onClose={onClose}>
      <p className="muted small">This channel requires a password to join.</p>
      <input
        type="password"
        placeholder="Channel password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit()}
        autoFocus
      />
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={!password || busy} onClick={submit}>
        {busy ? "Joining…" : "Join"}
      </button>
    </Modal>
  );
}

// Start a conversation: one person picked = a 1:1 DM, two or more = a group.
function NewDM({ onClose, onOpened, onCreateGroup }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState([]);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const selectedIds = new Set(selected.map((u) => u.id));
  const isGroup = selected.length >= 2;

  async function search(value) {
    setQ(value);
    if (value.trim().length < 2) return setResults([]);
    try {
      const rows = await api(`/users/search?q=${encodeURIComponent(value)}`);
      setResults(rows.filter((u) => !selectedIds.has(u.id)));
    } catch {
      setResults([]);
    }
  }

  function toggle(u) {
    setSelected((prev) =>
      prev.some((x) => x.id === u.id)
        ? prev.filter((x) => x.id !== u.id)
        : [...prev, u]
    );
    setQ("");
    setResults([]);
  }

  async function go() {
    setBusy(true);
    setError("");
    try {
      if (selected.length === 1) {
        const ch = await api("/dms", {
          method: "POST",
          body: { user_id: selected[0].id },
        });
        onOpened(ch.id);
      } else {
        const g = await onCreateGroup(
          selected.map((u) => u.id),
          name.trim()
        );
        onOpened(g.id);
      }
    } catch (e) {
      setError(e.message);
      setBusy(false);
    }
  }

  return (
    <Modal title="New conversation" onClose={onClose}>
      {selected.length > 0 && (
        <div className="chips">
          {selected.map((u) => (
            <button
              key={u.id}
              className="chip"
              onClick={() => toggle(u)}
              title="Remove"
            >
              {u.display_name} ✕
            </button>
          ))}
        </div>
      )}
      <input
        placeholder="Search people…"
        value={q}
        onChange={(e) => search(e.target.value)}
        autoFocus
      />
      <div className="results">
        {results.map((u) => (
          <button key={u.id} className="result" onClick={() => toggle(u)}>
            <Avatar name={u.display_name} admin={u.is_admin} size="sm" />
            <span>
              {u.display_name} <span className="muted">@{u.username}</span>
            </span>
          </button>
        ))}
      </div>
      {isGroup && (
        <input
          placeholder="Group name (optional)"
          value={name}
          maxLength={64}
          onChange={(e) => setName(e.target.value)}
        />
      )}
      {error && <div className="error">{error}</div>}
      {selected.length > 0 && (
        <button className="primary" disabled={busy} onClick={go}>
          {busy
            ? isGroup
              ? "Creating…"
              : "Opening…"
            : isGroup
            ? `Create group (${selected.length})`
            : `Message ${selected[0].display_name}`}
        </button>
      )}
    </Modal>
  );
}

function Modal({ title, children, onClose }) {
  const dialogRef = useDialog(onClose);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="link" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
