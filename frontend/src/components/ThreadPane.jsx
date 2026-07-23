import { useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import Avatar from "./Avatar.jsx";
import MessageContent from "./MessageContent.jsx";

// Right-hand thread panel: the root message + its replies + a reply composer.
// `messages` is [root, ...replies]. New replies arrive live via the WS event.
export default function ThreadPane({
  channel,
  messages,
  onClose,
  onOpenProfile,
  canPost = true,
}) {
  const { user } = useAuth();
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const bodyRef = useRef(null);
  const rootId = messages[0]?.id;

  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  async function send(e) {
    e.preventDefault();
    const content = text.trim();
    if (!content || !rootId) return;
    setText("");
    setError("");
    try {
      await api(`/channels/${channel.id}/messages`, {
        method: "POST",
        body: { content, thread_root_id: rootId },
      });
    } catch (err) {
      setError(err.message);
      setText(content);
    }
  }

  const root = messages[0];
  const replies = messages.slice(1);

  return (
    <aside className="thread-pane">
      <div className="thread-head">
        <span className="thread-title">Thread</span>
        <button className="link" onClick={onClose} title="Close thread">
          ✕
        </button>
      </div>
      <div className="thread-body" ref={bodyRef}>
        {root && (
          <ThreadMsg m={root} myId={user.id} onOpenProfile={onOpenProfile} root />
        )}
        {replies.length > 0 && (
          <div className="thread-divider">
            {replies.length} {replies.length === 1 ? "reply" : "replies"}
          </div>
        )}
        {replies.map((m) => (
          <ThreadMsg
            key={m.id}
            m={m}
            myId={user.id}
            onOpenProfile={onOpenProfile}
          />
        ))}
      </div>
      {error && <div className="error compose-error">{error}</div>}
      {canPost ? (
        <form className="composer" onSubmit={send}>
          <input
            placeholder="Reply in thread…"
            value={text}
            autoFocus
            onChange={(e) => setText(e.target.value)}
          />
          <button className="primary" disabled={!text.trim()}>
            Send
          </button>
        </form>
      ) : (
        <div className="readonly-note">🔒 React only — replies are disabled.</div>
      )}
    </aside>
  );
}

function ThreadMsg({ m, myId, onOpenProfile, root }) {
  return (
    <div className={`thread-msg ${root ? "root" : ""}`}>
      <Avatar name={m.sender?.display_name} admin={m.sender?.is_admin} />
      <div className="thread-msg-body">
        <div className="msg-meta">
          <button
            className="msg-author-btn"
            onClick={() => m.sender_id && onOpenProfile?.(m.sender_id)}
          >
            {m.sender?.display_name || "Unknown"}
          </button>
          <span className="msg-time">
            {new Date(m.created_at).toLocaleTimeString([], {
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
        </div>
        <div className="msg-text">
          <MessageContent
            content={m.content}
            mentions={m.mentions}
            myId={myId}
            onOpenProfile={onOpenProfile}
          />
          {m.edited_at && <span className="edited">(edited)</span>}
        </div>
        {m.reactions?.length > 0 && (
          <div className="reactions">
            {m.reactions.map((r) => (
              <span key={r.emoji} className={`reaction ${r.me ? "me" : ""}`}>
                <span>{r.emoji}</span>
                <span className="rcount">{r.count}</span>
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
