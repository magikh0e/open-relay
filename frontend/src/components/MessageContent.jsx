// Renders message text with @mentions highlighted.
//
// SECURITY: this is the one place we parse message text for display. It builds
// an array of React nodes (plain strings + <button> elements). Strings become
// text nodes and elements are created via JSX, so React escapes everything —
// there is NO innerHTML / dangerouslySetInnerHTML anywhere. A message whose
// text is `<script>…` renders as the literal characters, never as markup.

const MENTION_RE = /@([a-zA-Z0-9_.-]{3,32})/g;

// Only render images from Giphy's CDN (host anchored) — never arbitrary URLs.
const GIPHY_RE =
  /^https:\/\/(?:media\d*\.giphy\.com|i\.giphy\.com)\/[^\s"'<>]+\.(?:gif|webp)(?:\?[^\s"'<>]*)?$/i;

export default function MessageContent({ content, mentions = [], myId, onOpenProfile }) {
  if (!content) return null;

  const trimmed = content.trim();
  if (GIPHY_RE.test(trimmed)) {
    return (
      <img
        className="gif-msg"
        src={trimmed}
        alt="GIF"
        loading="lazy"
        referrerPolicy="no-referrer"
      />
    );
  }

  // Only highlight tokens the server actually resolved to real users.
  const byName = new Map(mentions.map((m) => [m.username.toLowerCase(), m]));

  const nodes = [];
  let last = 0;
  let match;
  MENTION_RE.lastIndex = 0;

  while ((match = MENTION_RE.exec(content)) !== null) {
    const [full, name] = match;
    const start = match.index;
    const prev = start > 0 ? content[start - 1] : "";
    const mention = byName.get(name.toLowerCase());

    // Match backend rules: real mention, and not email-like (preceded by word/dot).
    if (mention && !/[\w.]/.test(prev)) {
      if (start > last) nodes.push(content.slice(last, start));
      const isMe = mention.id === myId;
      nodes.push(
        <button
          key={`m${start}`}
          type="button"
          className={`mention ${isMe ? "mention-me" : ""}`}
          onClick={() => onOpenProfile?.(mention.id)}
          title={`@${mention.username}`}
        >
          @{mention.display_name}
        </button>
      );
      last = start + full.length;
    }
  }
  if (last < content.length) nodes.push(content.slice(last));

  return <>{nodes}</>;
}
