import { useEffect, useState } from "react";
import { api } from "../api.js";
import { useDialog } from "../useDialog.js";

// Highlight query matches by splitting into React text nodes + <mark> — never
// innerHTML, so it's XSS-safe.
function highlight(text, q) {
  if (!q) return text;
  const parts = [];
  const lower = text.toLowerCase();
  const lq = q.toLowerCase();
  let i = 0;
  let idx;
  while ((idx = lower.indexOf(lq, i)) !== -1) {
    if (idx > i) parts.push(text.slice(i, idx));
    parts.push(<mark key={idx}>{text.slice(idx, idx + q.length)}</mark>);
    i = idx + q.length;
  }
  if (i < text.length) parts.push(text.slice(i));
  return parts;
}

export default function SearchModal({ onClose, onOpen }) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const dialogRef = useDialog(onClose);

  useEffect(() => {
    const query = q.trim();
    if (query.length < 2) {
      setResults([]);
      setSearched(false);
      return;
    }
    let alive = true;
    setLoading(true);
    const t = setTimeout(async () => {
      try {
        const r = await api(`/search?q=${encodeURIComponent(query)}`);
        if (alive) {
          setResults(r);
          setSearched(true);
        }
      } catch {
        if (alive) setResults([]);
      } finally {
        if (alive) setLoading(false);
      }
    }, 250);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  const prefix = (kind) => (kind === "dm" ? "@ " : kind === "private" ? "🔒 " : "# ");

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal search-modal"
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <input
            className="search-input"
            autoFocus
            placeholder="Search messages…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
            }}
          />
          <button className="link" onClick={onClose}>
            ✕
          </button>
        </div>
        <div className="search-note muted small">
          🔒 Encrypted direct messages aren't searchable — the server can't read
          them to index them.
        </div>
        <div className="search-results">
          {loading && <div className="muted small">Searching…</div>}
          {!loading && searched && results.length === 0 && (
            <div className="muted small">No messages found.</div>
          )}
          {results.map((r) => (
            <button
              key={r.id}
              className="search-result"
              onClick={() => onOpen(r.channel_id, r.id)}
            >
              <div className="search-meta">
                <span className="search-channel">
                  {prefix(r.channel_kind)}
                  {r.channel_name}
                </span>
                <span className="search-author">
                  {r.sender?.display_name || "Unknown"}
                </span>
                <span className="search-time">
                  {new Date(r.created_at).toLocaleDateString()}
                </span>
              </div>
              <div className="search-snippet">
                {highlight(r.content, q.trim())}
              </div>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
