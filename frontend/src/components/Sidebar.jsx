import { useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import Avatar from "./Avatar.jsx";

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
}) {
  const { user, logout } = useAuth();
  const [creating, setCreating] = useState(false);
  const [dmSearch, setDmSearch] = useState(false);

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
        <button className="link" onClick={logout} title="Log out">
          ⏻
        </button>
      </div>

      <button className="search-trigger" onClick={onOpenSearch}>
        <span>🔍</span> Search messages
      </button>

      <div className="section">
        <div className="section-head">
          <span>Channels</span>
          <button className="link" onClick={() => setCreating(true)}>
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
              {c.read_only ? "📣" : c.kind === "private" ? "🔒" : "#"}
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
              <span className="hash">#</span>
              <span className="row-name">{c.name}</span>
              <button className="mini" onClick={() => onJoin(c)}>
                Join
              </button>
            </div>
          ))}
        </div>
      )}

      <div className="section">
        <div className="section-head">
          <span>Direct Messages</span>
          <button className="link" onClick={() => setDmSearch(true)}>
            +
          </button>
        </div>
        {dms.map((c) => {
          // DM "topic" carries the other user's username; name is display name.
          return (
            <div key={c.id} className="dm-row-wrap">
              <button
                className={`row ${c.id === activeId ? "active" : ""}`}
                onClick={() => onOpen(c.id)}
              >
                <span className="dot-name">
                  <span className="row-name">{c.name}</span>
                </span>
                {(c.mention_count > 0 || c.unread_count > 0) && (
                <span className={`unread-badge ${c.mention_count > 0 ? "mention" : ""}`}>
                  {c.mention_count > 0 ? `@${c.mention_count}` : c.unread_count}
                </span>
              )}
              </button>
              {onCloseDm && (
                <button
                  className="dm-close"
                  title="Close this conversation (hides it for you only)"
                  onClick={(e) => {
                    e.stopPropagation();
                    onCloseDm(c);
                  }}
                >
                  ✕
                </button>
              )}
            </div>
          );
        })}
      </div>

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
          onOpened={async (id) => {
            setDmSearch(false);
            await onRefresh();
            onOpen(id);
          }}
        />
      )}
    </aside>
  );
}

function CreateChannel({ onClose, onCreated }) {
  const [name, setName] = useState("");
  const [isPrivate, setIsPrivate] = useState(false);
  const [error, setError] = useState("");

  const slug = name
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, "-")
    .replace(/^-+|-+$/g, "");

  async function create() {
    setError("");
    try {
      const ch = await api("/channels", {
        method: "POST",
        body: { slug, name, topic: "", is_private: isPrivate },
      });
      onCreated(ch.id);
    } catch (e) {
      setError(e.message);
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
      {error && <div className="error">{error}</div>}
      <button className="primary" disabled={slug.length < 2} onClick={create}>
        Create
      </button>
    </Modal>
  );
}

function NewDM({ onClose, onOpened }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);

  async function search(value) {
    setQ(value);
    if (value.trim().length < 2) return setResults([]);
    setResults(await api(`/users/search?q=${encodeURIComponent(value)}`));
  }

  async function openDm(userId) {
    const ch = await api("/dms", { method: "POST", body: { user_id: userId } });
    onOpened(ch.id);
  }

  return (
    <Modal title="New direct message" onClose={onClose}>
      <input
        placeholder="Search people…"
        value={q}
        onChange={(e) => search(e.target.value)}
        autoFocus
      />
      <div className="results">
        {results.map((u) => (
          <button key={u.id} className="result" onClick={() => openDm(u.id)}>
            <span className="avatar sm">{u.display_name[0]?.toUpperCase()}</span>
            <span>
              {u.display_name} <span className="muted">@{u.username}</span>
            </span>
          </button>
        ))}
      </div>
    </Modal>
  );
}

function Modal({ title, children, onClose }) {
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="link" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}
