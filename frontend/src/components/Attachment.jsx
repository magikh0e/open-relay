import { useEffect, useState } from "react";
import { decryptFile } from "../e2ee.js";
import { resolveUrl } from "../config.js";

function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Renders a message's file attachment: images inline, everything else as a
// download card. The URL is same-origin (/api/uploads/<id>).
//
// Encrypted attachments are fetched as ciphertext and decrypted here into an
// object URL — the server holds neither the plaintext nor the real filename.
export default function Attachment({ attachment, dmKey }) {
  const { url, name, size, is_image, encrypted, enc_meta } = attachment;
  const [decoded, setDecoded] = useState(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!encrypted || !dmKey) return undefined;
    let alive = true;
    let objectUrl = null;
    (async () => {
      try {
        const res = await fetch(resolveUrl(url));
        if (!res.ok) throw new Error("fetch failed");
        const out = await decryptFile(dmKey, await res.arrayBuffer(), enc_meta);
        if (!alive) {
          URL.revokeObjectURL(out.url);
          return;
        }
        objectUrl = out.url;
        setDecoded(out);
      } catch {
        if (alive) setFailed(true);
      }
    })();
    return () => {
      alive = false;
      // Object URLs pin the decrypted bytes in memory until released.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [encrypted, dmKey, url, enc_meta]);

  if (encrypted) {
    if (failed) {
      return <div className="file-card muted">🔒 Can't decrypt this file</div>;
    }
    if (!decoded) {
      return <div className="file-card muted">🔒 Decrypting…</div>;
    }
    return (decoded.type || "").startsWith("image/") ? (
      <a
        className="attachment-link"
        href={decoded.url}
        target="_blank"
        rel="noreferrer"
      >
        <img className="attachment-img" src={decoded.url} alt={decoded.name} />
      </a>
    ) : (
      <a className="file-card" href={decoded.url} download={decoded.name}>
        <span className="file-icon">🔒</span>
        <span className="file-info">
          <span className="file-name">{decoded.name}</span>
          <span className="file-size">{formatSize(size)} · encrypted</span>
        </span>
        <span className="file-dl">⤓</span>
      </a>
    );
  }

  const src = resolveUrl(url);
  if (is_image) {
    return (
      <a className="attachment-link" href={src} target="_blank" rel="noreferrer">
        <img className="attachment-img" src={src} alt={name} loading="lazy" />
      </a>
    );
  }
  return (
    <a className="file-card" href={src} target="_blank" rel="noreferrer" download>
      <span className="file-icon">📄</span>
      <span className="file-info">
        <span className="file-name">{name}</span>
        <span className="file-size">{formatSize(size)}</span>
      </span>
      <span className="file-dl">⤓</span>
    </a>
  );
}
