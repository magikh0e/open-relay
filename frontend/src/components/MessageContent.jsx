// Renders message text with @mentions highlighted.
//
// SECURITY: this is the one place we parse message text for display. It builds
// an array of React nodes (plain strings + <button> elements). Strings become
// text nodes and elements are created via JSX, so React escapes everything —
// there is NO innerHTML / dangerouslySetInnerHTML anywhere. A message whose
// text is `<script>…` renders as the literal characters, never as markup.

const MENTION_RE = /@([a-zA-Z0-9_.-]{3,32})/g;

// Inline images are rendered ONLY from trusted, host-anchored CDNs — never an
// arbitrary URL. Returns a safe image URL to embed, or null.
const GIPHY_RE =
  /^https:\/\/(?:media\d*\.giphy\.com|i\.giphy\.com)\/[^\s"'<>]+\.(?:gif|webp)(?:\?[^\s"'<>]*)?$/i;
const IMGUR_DIRECT_RE =
  /^https:\/\/i\.imgur\.com\/[a-zA-Z0-9]+\.(?:gif|gifv|png|jpe?g|webp)(?:\?[^\s"'<>]*)?$/i;
const IMGUR_PAGE_RE = /^https?:\/\/(?:www\.)?imgur\.com\/([a-zA-Z0-9]{5,12})$/i;

function embedUrl(text) {
  if (GIPHY_RE.test(text)) return text;
  if (IMGUR_DIRECT_RE.test(text)) return text.replace(/\.gifv(\?|$)/i, ".gif$1");
  const page = text.match(IMGUR_PAGE_RE);
  if (page) return `https://i.imgur.com/${page[1]}.jpeg`;
  return null;
}

export default function MessageContent({ content, mentions = [], myId, onOpenProfile }) {
  if (!content) return null;

  const media = embedUrl(content.trim());
  if (media) {
    return (
      <img
        className="gif-msg"
        src={media}
        alt="image"
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
