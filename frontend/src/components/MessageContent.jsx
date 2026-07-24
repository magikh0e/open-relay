// Renders message text with @mentions highlighted and light markdown-style
// formatting (code, bold, italic, strikethrough).
//
// SECURITY: this is the one place we parse message text for display. It builds
// an array of React nodes (plain strings + elements). Strings become text nodes
// and elements are created via JSX, so React escapes everything — there is NO
// innerHTML / dangerouslySetInnerHTML anywhere. A message whose text is
// `<script>…` renders as the literal characters, never as markup. Adding
// formatting does NOT change that: every match produces a JSX element wrapping
// escaped text, never a string of HTML.

const MENTION_RE = /@([a-zA-Z0-9_.-]{3,32})/g;

// Fenced code blocks, with an optional language tag we ignore.
//
// The language tag only counts when a newline follows it. Matching it as a
// bare `[^\n]*` swallowed the code on a single-line ```like this``` fence,
// leaving an empty block — which was every fence, since the composer couldn't
// produce newlines at the time.
const FENCE_RE = /```(?:[a-zA-Z0-9+#._-]*\n)?([\s\S]*?)```/g;

// Inline spans. Order matters: `code` first so formatting characters inside
// backticks stay literal, and ** before * so bold wins over italic.
const INLINE_RE = /`([^`\n]+)`|\*\*([^*\n]+)\*\*|\*([^*\n]+)\*|~~([^~\n]+)~~/g;

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

/** Apply inline formatting to a run of plain text. */
function formatInline(text, keyBase) {
  const nodes = [];
  let last = 0;
  let m;
  INLINE_RE.lastIndex = 0;
  while ((m = INLINE_RE.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const key = `${keyBase}i${m.index}`;
    const [, code, bold, italic, strike] = m;
    if (code !== undefined) nodes.push(<code key={key}>{code}</code>);
    else if (bold !== undefined) nodes.push(<strong key={key}>{bold}</strong>);
    else if (italic !== undefined) nodes.push(<em key={key}>{italic}</em>);
    else nodes.push(<del key={key}>{strike}</del>);
    last = m.index + m[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

/** Mentions, then inline formatting on whatever text remains around them. */
function formatText(text, keyBase, byName, myId, onOpenProfile) {
  const nodes = [];
  let last = 0;
  let match;
  MENTION_RE.lastIndex = 0;

  while ((match = MENTION_RE.exec(text)) !== null) {
    const [full, name] = match;
    const start = match.index;
    const prev = start > 0 ? text[start - 1] : "";
    const mention = byName.get(name.toLowerCase());

    // Match backend rules: real mention, and not email-like (preceded by word/dot).
    if (mention && !/[\w.]/.test(prev)) {
      if (start > last) {
        nodes.push(...formatInline(text.slice(last, start), `${keyBase}t${last}`));
      }
      const isMe = mention.id === myId;
      nodes.push(
        <button
          key={`${keyBase}m${start}`}
          type="button"
          className={`mention ${isMe ? "mention-me" : ""}`}
          onClick={() => onOpenProfile?.(mention.id)}
          title={`@${mention.username}`}
          aria-label={`@${mention.username}`}
        >
          @{mention.display_name}
        </button>
      );
      last = start + full.length;
    }
  }
  if (last < text.length) {
    nodes.push(...formatInline(text.slice(last), `${keyBase}t${last}`));
  }
  return nodes;
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

  // Split out fenced code first — nothing inside a code block is parsed for
  // mentions or formatting, which is the whole point of pasting code.
  const nodes = [];
  let last = 0;
  let fence;
  FENCE_RE.lastIndex = 0;
  while ((fence = FENCE_RE.exec(content)) !== null) {
    if (fence.index > last) {
      nodes.push(
        ...formatText(
          content.slice(last, fence.index),
          `f${last}`,
          byName,
          myId,
          onOpenProfile
        )
      );
    }
    nodes.push(
      <pre className="code-block" key={`c${fence.index}`}>
        <code>{fence[1].replace(/\n$/, "")}</code>
      </pre>
    );
    last = fence.index + fence[0].length;
  }
  if (last < content.length) {
    nodes.push(
      ...formatText(content.slice(last), `f${last}`, byName, myId, onOpenProfile)
    );
  }

  return <>{nodes}</>;
}
