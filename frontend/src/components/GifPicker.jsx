import { useEffect, useState } from "react";
import { api } from "../api.js";

// Popover GIF picker backed by the server-side Giphy proxy. Selecting a GIF
// calls onPick(url) with the Giphy CDN url (sent as a message).
export default function GifPicker({ onPick, onClose }) {
  const [q, setQ] = useState("");
  const [gifs, setGifs] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    const query = q.trim();
    const t = setTimeout(async () => {
      try {
        const path = query
          ? `/giphy/search?q=${encodeURIComponent(query)}`
          : "/giphy/trending";
        const res = await api(path);
        if (alive) setGifs(res || []);
      } catch {
        if (alive) setGifs([]);
      } finally {
        if (alive) setLoading(false);
      }
    }, 300);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [q]);

  return (
    <div className="gif-popover" onClick={(e) => e.stopPropagation()}>
      <div className="gif-head">
        <input
          className="gif-search"
          autoFocus
          placeholder="Search GIFs…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && onClose()}
        />
        <span className="gif-brand">via GIPHY</span>
      </div>
      <div className="gif-grid">
        {loading && <div className="muted small">Loading…</div>}
        {!loading && gifs.length === 0 && (
          <div className="muted small">No GIFs found.</div>
        )}
        {gifs.map((g) => (
          <button
            key={g.id}
            className="gif-cell"
            title={g.title}
            onClick={() => onPick(g.url)}
          >
            <img src={g.preview} alt={g.title} loading="lazy" referrerPolicy="no-referrer" />
          </button>
        ))}
      </div>
    </div>
  );
}
