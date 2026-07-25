import { useEffect, useRef, useState } from "react";
import { api, tokens } from "../api.js";
import { API_BASE } from "../config.js";
import { useAuth } from "../auth.jsx";
import { useSwipe } from "../useSwipe.js";
import { maybeCompressImage } from "../imageCompress.js";
import { encryptFile } from "../e2ee.js";
import MessageContent from "./MessageContent.jsx";
import Avatar from "./Avatar.jsx";
import GifPicker from "./GifPicker.jsx";
import Attachment from "./Attachment.jsx";

const QUICK_EMOJI = ["👍", "❤️", "😂", "🎉", "😮", "😢", "🔥", "✅", "🤙", "🍆", "😎"];

export default function MessagePane({
  channel,
  messages,
  typing,
  online,
  onSent,
  onPrepend,
  onTyping,
  onOpenProfile,
  canDelete,
  onDeleteChannel,
  canManage,
  onSetTopic,
  onOpenSettings,
  onOpenThread,
  onCommand,
  onBack,
  onToggleRoster,
  canPost = true,
  jumpTo = null,
  onJumped,
  decrypted = {},
  encryptContent = null,
  e2ee = null,
  dmKey = null,
}) {
  const { user } = useAuth();
  // Drafts are per channel and survive switching away (and a reload). Kept in
  // localStorage rather than state so they outlive the component.
  const draftKey = `relay_draft_${channel.id}`;
  const [text, setText] = useState(() => {
    try {
      return localStorage.getItem(`relay_draft_${channel.id}`) || "";
    } catch {
      return "";
    }
  });
  const [error, setError] = useState("");
  const [editingId, setEditingId] = useState(null);
  const [editText, setEditText] = useState("");
  const [pickerFor, setPickerFor] = useState(null); // message id showing emoji picker
  const [replyingTo, setReplyingTo] = useState(null); // {id, sender_name, content}
  const [editingTopic, setEditingTopic] = useState(false);
  const [topicText, setTopicText] = useState("");
  const [mentionResults, setMentionResults] = useState([]);
  const [activeMention, setActiveMention] = useState(0);
  const [note, setNote] = useState(null); // slash-command feedback {ok, text}
  const [gifOpen, setGifOpen] = useState(false);
  const [gifEnabled, setGifEnabled] = useState(false);
  const [pendingAttachment, setPendingAttachment] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [activeMsgId, setActiveMsgId] = useState(null); // mobile: tap to reveal actions
  const [showFingerprint, setShowFingerprint] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [exhausted, setExhausted] = useState(false);
  const fileInputRef = useRef(null);
  const dragDepth = useRef(0);

  // Drag-and-drop: accept a file dropped anywhere on the pane. Only react to
  // actual file drags (not text/selection drags), and use an enter/leave depth
  // counter so moving over child elements doesn't flicker the overlay.
  const isFileDrag = (e) =>
    Array.from(e.dataTransfer?.types || []).includes("Files");

  function onDragEnter(e) {
    if (!canPost || !isFileDrag(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  }
  function onDragOver(e) {
    if (!isFileDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
  }
  function onDragLeave(e) {
    if (!isFileDrag(e)) return;
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setDragging(false);
    }
  }
  function onDrop(e) {
    if (!canPost || !isFileDrag(e)) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const file = e.dataTransfer.files?.[0];
    if (file) uploadFile(file);
  }

  // Mobile swipe navigation: right → back to channel list, left → open roster.
  const swipe = useSwipe({
    onSwipeRight: onBack || undefined,
    onSwipeLeft: onToggleRoster || undefined,
  });

  async function uploadFile(file) {
    if (!file || !canPost) return;
    setUploading(true);
    setError("");
    try {
      // Re-encode images client-side before uploading (docs/GIFs pass through).
      // Throws if a raster image can't be re-encoded, aborting the upload so an
      // un-stripped original is never sent.
      const toSend = await maybeCompressImage(file);
      const form = new FormData();
      if (dmKey) {
        // Encrypted conversation: the file is sealed with the same key as the
        // messages, so the server stores opaque bytes and never learns the
        // real name or type.
        const { blob, meta } = await encryptFile(dmKey, toSend);
        form.append("file", blob, "blob.bin");
        form.append("encrypted", "true");
        form.append("enc_meta", meta);
      } else {
        form.append("file", toSend);
      }
      const res = await fetch(`${API_BASE}/uploads`, {
        method: "POST",
        headers: { Authorization: `Bearer ${tokens.access}` },
        body: form,
      });
      if (!res.ok) {
        const e = await res.json().catch(() => null);
        throw new Error(e?.detail || "Upload failed");
      }
      setPendingAttachment(await res.json());
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  useEffect(() => {
    api("/giphy/enabled")
      .then((r) => setGifEnabled(!!r?.enabled))
      .catch(() => {});
  }, []);

  async function sendGif(url) {
    setGifOpen(false);
    const replyId = replyingTo?.id || null;
    setReplyingTo(null);
    try {
      const msg = await api(`/channels/${channel.id}/messages`, {
        method: "POST",
        body: { content: url, reply_to_id: replyId },
      });
      onSent(msg);
    } catch (err) {
      setError(err.message);
    }
  }
  const messagesRef = useRef(null);
  const nearBottomRef = useRef(true);
  const inputRef = useRef(null);
  const lastTypingSent = useRef(0);

  // Track whether the user is pinned near the bottom (vs. scrolled up reading),
  // and pull older history in when they reach the top.
  function handleScroll() {
    const el = messagesRef.current;
    if (!el) return;
    const gap = el.scrollHeight - el.scrollTop - el.clientHeight;
    nearBottomRef.current = gap < 120;
    if (el.scrollTop < 80) loadOlder();
  }

  async function loadOlder() {
    const el = messagesRef.current;
    if (!el || loadingOlder || exhausted || !messages.length) return;
    setLoadingOlder(true);
    try {
      const oldest = messages[0].created_at;
      const older = await api(
        `/channels/${channel.id}/messages?before=${encodeURIComponent(oldest)}&limit=50`
      );
      if (!older.length) {
        setExhausted(true);
        return;
      }
      // Keep the viewport steady: prepending grows the scroll height, so
      // restore the offset from the bottom rather than the top.
      const prevHeight = el.scrollHeight;
      const prevTop = el.scrollTop;
      onPrepend?.(older);
      requestAnimationFrame(() => {
        el.scrollTop = el.scrollHeight - prevHeight + prevTop;
      });
    } catch {
      /* leave it; the user can scroll again to retry */
    } finally {
      setLoadingOlder(false);
    }
  }

  // Auto-scroll ONLY the messages container (never the window), and only when
  // the user is already at the bottom — so reading history isn't interrupted.
  useEffect(() => {
    const el = messagesRef.current;
    if (el && nearBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length]);

  useEffect(() => {
    setExhausted(false);
    try {
      setText(localStorage.getItem(`relay_draft_${channel.id}`) || "");
    } catch {
      setText("");
    }
  }, [channel.id]);

  // Persist the draft as it changes; clear the entry when it's empty so we
  // don't accumulate junk keys for every channel ever visited.
  useEffect(() => {
    try {
      if (text) localStorage.setItem(draftKey, text);
      else localStorage.removeItem(draftKey);
    } catch {
      /* private mode / quota: drafts are a convenience, not critical */
    }
  }, [text, draftKey]);

  // Land on a message opened from search: scroll it into view and flash it so
  // the eye can find it among its neighbours.
  useEffect(() => {
    if (!jumpTo) return;
    const el = messagesRef.current?.querySelector(`[data-mid="${jumpTo}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "center" });
    el.classList.add("jump-flash");
    const t = setTimeout(() => {
      el.classList.remove("jump-flash");
      onJumped?.();
    }, 2000);
    return () => clearTimeout(t);
  }, [jumpTo, messages.length, onJumped]);

  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 5000);
    return () => clearTimeout(t);
  }, [note]);

  async function send(e) {
    e.preventDefault();
    const content = text.trim();
    if (!content && !pendingAttachment) return;

    // Slash command (a leading "//" escapes a literal message starting with /).
    if (
      content.startsWith("/") &&
      !content.startsWith("//") &&
      !pendingAttachment
    ) {
      setText("");
      setReplyingTo(null);
      setMentionResults([]);
      setError("");
      try {
        const res = await onCommand?.(content);
        setNote({ ok: res?.ok !== false, text: res?.message || "" });
      } catch (err) {
        setNote({ ok: false, text: err.message });
      }
      return;
    }

    const replyId = replyingTo?.id || null;
    const uploadId = pendingAttachment?.id || null;
    setText("");
    setReplyingTo(null);
    setMentionResults([]);
    setPendingAttachment(null);
    setError("");
    try {
      let body = content.startsWith("//") ? content.slice(1) : content;
      let encrypted = false;
      // In an encryption-ready DM the ciphertext is all the server ever sees.
      if (encryptContent && body) {
        const sealed = await encryptContent(body);
        if (sealed) {
          body = sealed;
          encrypted = true;
        }
      }
      const msg = await api(`/channels/${channel.id}/messages`, {
        method: "POST",
        body: {
          content: body,
          reply_to_id: replyId,
          upload_id: uploadId,
          encrypted,
        },
      });
      onSent(msg);
    } catch (err) {
      setError(err.message);
      setText(content);
      setPendingAttachment(pendingAttachment);
    }
  }

  function handleChange(e) {
    const value = e.target.value;
    setText(value);
    // Grow with the content up to a ceiling, then scroll internally.
    const el = e.target;
    if (el.style) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
    }
    const now = Date.now();
    if (now - lastTypingSent.current > 2000) {
      lastTypingSent.current = now;
      onTyping();
    }
    detectMention(value, e.target.selectionStart);
  }

  // @mention autocomplete: find the @token under the caret and suggest users.
  async function detectMention(value, caret) {
    const upto = value.slice(0, caret ?? value.length);
    const m = /(?:^|\s)@([a-zA-Z0-9_.-]*)$/.exec(upto);
    if (!m || m[1].length < 2) {
      setMentionResults([]);
      return;
    }
    try {
      const users = await api(`/users/search?q=${encodeURIComponent(m[1])}`);
      setMentionResults(users.slice(0, 6));
      setActiveMention(0);
    } catch {
      setMentionResults([]);
    }
  }

  // Keyboard control for the @mention menu: arrows to move, Enter/Tab to
  // complete, Esc to dismiss. Falls through to normal typing when closed.
  function onComposerKeyDown(e) {
    // In a textarea Enter would insert a newline, so sending is explicit.
    // Shift+Enter (or Ctrl/Cmd+Enter) is how you get an actual line break —
    // which is what makes multi-line code blocks possible to type at all.
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !e.ctrlKey &&
      !e.metaKey &&
      mentionResults.length === 0
    ) {
      e.preventDefault();
      send(e);
      return;
    }
    if (mentionResults.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveMention((i) => (i + 1) % mentionResults.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveMention(
        (i) => (i - 1 + mentionResults.length) % mentionResults.length
      );
    } else if (e.key === "Enter" || e.key === "Tab") {
      // Complete the mention instead of sending the message / leaving the field.
      e.preventDefault();
      const pick = mentionResults[activeMention] || mentionResults[0];
      if (pick) insertMention(pick.username);
    } else if (e.key === "Escape") {
      e.preventDefault();
      setMentionResults([]);
    }
  }

  function insertMention(username) {
    const input = inputRef.current;
    const caret = input ? input.selectionStart : text.length;
    const before = text
      .slice(0, caret)
      .replace(/@([a-zA-Z0-9_.-]*)$/, `@${username} `);
    const after = text.slice(caret);
    const next = before + after;
    setText(next);
    setMentionResults([]);
    requestAnimationFrame(() => {
      if (input) {
        input.focus();
        input.setSelectionRange(before.length, before.length);
      }
    });
  }

  // Edit / react / delete all update state via the WebSocket broadcast the
  // server sends back, so these just fire the request and close local UI.
  async function saveEdit(id) {
    const content = editText.trim();
    setEditingId(null);
    if (!content) return;
    try {
      await api(`/channels/${channel.id}/messages/${id}`, {
        method: "PATCH",
        body: { content },
      });
    } catch (err) {
      setError(err.message);
    }
  }

  async function toggleReaction(id, emoji) {
    setPickerFor(null);
    try {
      await api(`/channels/${channel.id}/messages/${id}/reactions`, {
        method: "POST",
        body: { emoji },
      });
    } catch (err) {
      setError(err.message);
    }
  }

  async function remove(id) {
    try {
      await api(`/channels/${channel.id}/messages/${id}`, { method: "DELETE" });
    } catch (err) {
      setError(err.message);
    }
  }

  async function saveTopic() {
    setEditingTopic(false);
    try {
      await onSetTopic(topicText.trim());
    } catch (e) {
      setError(e.message);
    }
  }

  const typingIds = Object.keys(typing);
  const isDm = channel.kind === "dm";

  return (
    <main
      className="pane"
      onDragEnter={onDragEnter}
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      {...swipe}
    >
      {dragging && (
        <div className="drop-overlay">
          <div className="drop-hint">📎 Drop file to upload</div>
        </div>
      )}
      <header className="pane-head">
        <div className="pane-head-left">
          {onBack && (
            <button
              className="act mobile-only back-btn"
              title="Back to channels"
              onClick={onBack}
             aria-label="Back to channels">
              ‹
            </button>
          )}
          <span className="pane-title">
            {isDm
              ? ""
              : channel.kind === "private"
              ? "🔒 "
              : channel.kind === "group"
              ? "👥 "
              : "# "}
            {channel.name}
          </span>
          {!isDm &&
            channel.kind !== "group" &&
            (editingTopic ? (
              <span className="topic-edit">
                <input
                  className="topic-input"
                  value={topicText}
                  autoFocus
                  maxLength={512}
                  placeholder="Channel topic…"
                  onChange={(e) => setTopicText(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveTopic();
                    if (e.key === "Escape") setEditingTopic(false);
                  }}
                />
                <button className="mini" onClick={saveTopic}>
                  Save
                </button>
                <button
                  className="mini ghost"
                  onClick={() => setEditingTopic(false)}
                >
                  Cancel
                </button>
              </span>
            ) : canManage ? (
              <button
                className="pane-topic editable"
                title="Edit topic"
                onClick={() => {
                  setTopicText(channel.topic || "");
                  setEditingTopic(true);
                }}
              >
                {channel.topic || "Add a topic…"}
              </button>
            ) : (
              channel.topic && (
                <span className="pane-topic">{channel.topic}</span>
              )
            ))}
        </div>
        <div className="pane-head-right">
          {e2ee?.ready && (
            <button
              className="e2ee-badge"
              title="End-to-end encrypted — click to verify the connection"
              onClick={() => setShowFingerprint((v) => !v)}
            >
              🔒 Encrypted
            </button>
          )}
          {!isDm && (
            <span className="muted small member-count-label">
              {channel.member_count} members
            </span>
          )}
          {!isDm && onToggleRoster && (
            <button
              className="act mobile-only"
              title="Members"
              onClick={onToggleRoster}
             aria-label="Members">
              👥
            </button>
          )}
          {canManage && !isDm && (
            <button
              className="act"
              title="Channel settings"
              onClick={onOpenSettings}
             aria-label="Channel settings">
              ⚙
            </button>
          )}
          {canDelete && !isDm && !channel.read_only && (
            <button
              className="act danger"
              title="Delete channel"
              onClick={onDeleteChannel}
             aria-label="Delete channel">
              🗑
            </button>
          )}
        </div>
      </header>

      {showFingerprint && e2ee?.fingerprint && (
        <div className="fingerprint-panel">
          <div className="fingerprint-head">Safety number</div>
          <code className="fingerprint">{e2ee.fingerprint}</code>
          <p className="muted small">
            Read this aloud to {channel.name} — over the phone or in person. If
            your numbers match, nobody is sitting in the middle. If they don't,
            stop and don't share anything sensitive here.
          </p>
        </div>
      )}

      <div className="messages" ref={messagesRef} onScroll={handleScroll}>
        {loadingOlder && (
          <div className="load-older muted small">Loading earlier messages…</div>
        )}
        {messages.map((m, i) => {
          const prev = messages[i - 1];
          // Encrypted bodies are ciphertext until the decrypt pass fills them
          // in (undefined = still working, null = wrong/missing key).
          const shown = !m.encrypted
            ? m.content
            : decrypted[m.id] === undefined
            ? "🔒 Decrypting…"
            : decrypted[m.id] === null
            ? "🔒 Can't decrypt this message"
            : decrypted[m.id];
          const isAction = (shown || "").startsWith("/me ");
          const grouped =
            prev &&
            prev.sender_id === m.sender_id &&
            !m.edited_at &&
            !m.reply_to &&
            !isAction;
          const mine = m.sender_id === user.id;
          const editing = editingId === m.id;
          const mentionsMe = (m.mentions || []).some((x) => x.id === user.id);
          // No sender: a webhook post (author_name), else a seeded announcement.
          const authorName =
            m.sender?.display_name ||
            m.author_name ||
            (channel.read_only ? "Open Relay" : "Unknown");
          return (
            <div
              key={m.id}
              data-mid={m.id}
              className={`msg ${grouped ? "grouped" : ""} ${
                mentionsMe ? "mentions-me" : ""
              } ${activeMsgId === m.id ? "active" : ""}`}
              onClick={() =>
                setActiveMsgId((id) => (id === m.id ? null : m.id))
              }
            >
              {!grouped && (
                <Avatar name={authorName} admin={m.sender?.is_admin} />
              )}
              <div className="msg-body">
                {m.reply_to && (
                  <div className="reply-preview">
                    <span className="reply-arrow">↩</span>
                    <span className="reply-author">
                      {m.reply_to.sender_name}
                    </span>
                    <span className="reply-snippet">
                      {m.reply_to.encrypted
                        ? decrypted[m.reply_to.id] ?? "🔒 Encrypted message"
                        : m.reply_to.content}
                    </span>
                  </div>
                )}
                {!grouped && !isAction && (
                  <div className="msg-meta">
                    <span className="msg-author">
                      <button
                        className="msg-author-btn"
                        onClick={() =>
                          m.sender_id && onOpenProfile?.(m.sender_id)
                        }
                      >
                        {authorName}
                      </button>
                      {online.has(m.sender_id) && (
                        <span className="online-dot" title="online" />
                      )}
                    </span>
                    <span className="msg-time">
                      {new Date(m.created_at).toLocaleTimeString([], {
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </span>
                  </div>
                )}

                {editing ? (
                  <div className="edit-row">
                    <input
                      className="edit-input"
                      value={editText}
                      autoFocus
                      onChange={(e) => setEditText(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") saveEdit(m.id);
                        if (e.key === "Escape") setEditingId(null);
                      }}
                    />
                    <div className="edit-actions">
                      <button className="mini" onClick={() => saveEdit(m.id)}>
                        Save
                      </button>
                      <button
                        className="mini ghost"
                        onClick={() => setEditingId(null)}
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : isAction ? (
                  <div className="msg-action">
                    <span className="action-star">✷</span>{" "}
                    <button
                      className="msg-author-btn"
                      onClick={() =>
                        m.sender_id && onOpenProfile?.(m.sender_id)
                      }
                    >
                      {authorName}
                    </button>{" "}
                    <MessageContent
                      content={shown.slice(4)}
                      mentions={m.mentions}
                      myId={user.id}
                      onOpenProfile={onOpenProfile}
                    />
                    {m.edited_at && <span className="edited">(edited)</span>}
                  </div>
                ) : (
                  <div className={`msg-text ${mine ? "mine" : ""}`}>
                    <MessageContent
                      content={shown}
                      mentions={m.mentions}
                      myId={user.id}
                      onOpenProfile={onOpenProfile}
                    />
                    {m.edited_at && <span className="edited">(edited)</span>}
                  </div>
                )}

                {m.attachment && (
                  <Attachment attachment={m.attachment} dmKey={dmKey} />
                )}

                {/* reactions */}
                {(m.reactions?.length > 0 || pickerFor === m.id) && (
                  <div className="reactions">
                    {(m.reactions || []).map((r) => (
                      <button
                        key={r.emoji}
                        className={`reaction ${r.me ? "me" : ""}`}
                        onClick={() => toggleReaction(m.id, r.emoji)}
                        title={r.me ? "Remove your reaction" : "React"}
                      >
                        <span>{r.emoji}</span>
                        <span className="rcount">{r.count}</span>
                      </button>
                    ))}
                    {pickerFor === m.id && (
                      <div className="emoji-picker">
                        {QUICK_EMOJI.map((e) => (
                          <button
                            key={e}
                            className="emoji-opt"
                            onClick={() => toggleReaction(m.id, e)}
                          >
                            {e}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                )}

                {/* thread indicator (root messages with replies) */}
                {m.reply_count > 0 && (
                  <button
                    className="thread-indicator"
                    onClick={() => onOpenThread?.(m)}
                  >
                    🧵 {m.reply_count}{" "}
                    {m.reply_count === 1 ? "reply" : "replies"}
                    {m.last_reply_at && (
                      <span className="thread-last">
                        {" · last "}
                        {new Date(m.last_reply_at).toLocaleTimeString([], {
                          hour: "2-digit",
                          minute: "2-digit",
                        })}
                      </span>
                    )}
                  </button>
                )}
              </div>

              {/* hover actions */}
              {!editing && (
                <div className="msg-actions">
                  {canPost && (
                    <button
                      className="act"
                      title="Reply"
                      onClick={() => {
                        setReplyingTo({
                          id: m.id,
                          sender_name: m.sender?.display_name || "Unknown",
                          content: (shown || "").slice(0, 140),
                        });
                        requestAnimationFrame(() => inputRef.current?.focus());
                      }}
                     aria-label="Reply">
                      ↩
                    </button>
                  )}
                  {canPost && (
                    <button
                      className="act"
                      title="Reply in thread"
                      onClick={() => onOpenThread?.(m)}
                     aria-label="Reply in thread">
                      🧵
                    </button>
                  )}
                  <button
                    className="act"
                    title="React"
                    onClick={() =>
                      setPickerFor(pickerFor === m.id ? null : m.id)
                    }
                   aria-label="React">
                    🙂
                  </button>
                  {mine && !m.encrypted && (
                    <>
                      <button
                        className="act"
                        title="Edit"
                        onClick={() => {
                          setEditingId(m.id);
                          setEditText(m.content);
                        }}
                       aria-label="Edit">
                        ✎
                      </button>
                      <button
                        className="act"
                        title="Delete"
                        onClick={() => remove(m.id)}
                       aria-label="Delete">
                        🗑
                      </button>
                    </>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="typing-line">
        {typingIds.length > 0 && (
          <span className="muted small">
            {typingIds.length === 1 ? "Someone is" : `${typingIds.length} people are`}{" "}
            typing…
          </span>
        )}
      </div>

      {error && <div className="error compose-error">{error}</div>}
      {note && (
        <div className={`cmd-note ${note.ok ? "" : "err"}`}>
          {note.ok ? "✓ " : "⚠ "}
          {note.text}
        </div>
      )}

      {replyingTo && (
        <div className="reply-bar">
          <span className="reply-arrow">↩</span>
          <span className="reply-bar-text">
            Replying to <b>{replyingTo.sender_name}</b>
            <span className="reply-snippet"> — {replyingTo.content}</span>
          </span>
          <button
            className="link"
            title="Cancel reply"
            onClick={() => setReplyingTo(null)}
           aria-label="Cancel reply">
            ✕
          </button>
        </div>
      )}

      {mentionResults.length > 0 && (
        <div className="mention-menu">
          {mentionResults.map((u, i) => (
            <button
              key={u.id}
              className={`mention-opt ${i === activeMention ? "active" : ""}`}
              onMouseEnter={() => setActiveMention(i)}
              // onMouseDown (not onClick) so the input doesn't blur first.
              onMouseDown={(e) => {
                e.preventDefault();
                insertMention(u.username);
              }}
            >
              <span className="avatar sm">
                {u.display_name[0]?.toUpperCase()}
              </span>
              <span>
                {u.display_name} <span className="muted">@{u.username}</span>
              </span>
            </button>
          ))}
        </div>
      )}

      {e2ee && !e2ee.ready && (
        <div className="e2ee-bar">
          <span>
            🔓 Not encrypted —{" "}
            {e2ee.status === "none"
              ? "set up encryption to protect these messages."
              : e2ee.status === "locked"
              ? "unlock your key to encrypt these messages."
              : "the other person hasn't set up encryption yet."}
          </span>
          {e2ee.status !== "unlocked" && (
            <button className="mini" onClick={e2ee.onUnlock}>
              {e2ee.status === "none" ? "Set up" : "Unlock"}
            </button>
          )}
        </div>
      )}

      <div className="composer-wrap">
        {!canPost ? (
          <div className="readonly-note">
            🔒 Announcements only — react to updates below, but posting is
            disabled here.
          </div>
        ) : (
          <>
        {gifOpen && (
          <GifPicker onPick={sendGif} onClose={() => setGifOpen(false)} />
        )}
        {(pendingAttachment || uploading) && (
          <div className="attach-bar">
            {uploading ? (
              <span className="muted small">Uploading…</span>
            ) : (
              <>
                <span className="attach-chip">
                  📎 {pendingAttachment.name}
                </span>
                <button
                  className="link"
                  title="Remove attachment"
                  onClick={() => setPendingAttachment(null)}
                 aria-label="Remove attachment">
                  ✕
                </button>
              </>
            )}
          </div>
        )}
        <form className="composer" onSubmit={send}>
          <input
            type="file"
            ref={fileInputRef}
            style={{ display: "none" }}
            onChange={(e) => uploadFile(e.target.files?.[0])}
          />
          <button
            type="button"
            className="attach-btn"
            title="Attach a file"
            onClick={() => fileInputRef.current?.click()}
           aria-label="Attach a file">
            📎
          </button>
          {/* A textarea, not an input: code blocks and multi-line messages
              need real newlines. Enter still sends; Shift+Enter breaks the
              line. */}
          <textarea
            ref={inputRef}
            className="composer-input"
            rows={1}
            placeholder={`Message ${isDm ? channel.name : "#" + channel.name}`}
            value={text}
            onChange={handleChange}
            onKeyDown={onComposerKeyDown}
          />
          {gifEnabled && (
            <button
              type="button"
              className={`gif-btn ${gifOpen ? "active" : ""}`}
              title="Send a GIF"
              onClick={() => setGifOpen((o) => !o)}
            >
              GIF
            </button>
          )}
          <button
            className="primary"
            disabled={!text.trim() && !pendingAttachment}
          >
            Send
          </button>
        </form>
          </>
        )}
      </div>
    </main>
  );
}
