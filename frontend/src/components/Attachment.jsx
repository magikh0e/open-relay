function formatSize(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// Renders a message's file attachment: images inline, everything else as a
// download card. The URL is same-origin (/api/uploads/<id>).
export default function Attachment({ attachment }) {
  const { url, name, size, is_image } = attachment;
  if (is_image) {
    return (
      <a className="attachment-link" href={url} target="_blank" rel="noreferrer">
        <img className="attachment-img" src={url} alt={name} loading="lazy" />
      </a>
    );
  }
  return (
    <a className="file-card" href={url} target="_blank" rel="noreferrer" download>
      <span className="file-icon">📄</span>
      <span className="file-info">
        <span className="file-name">{name}</span>
        <span className="file-size">{formatSize(size)}</span>
      </span>
      <span className="file-dl">⤓</span>
    </a>
  );
}
