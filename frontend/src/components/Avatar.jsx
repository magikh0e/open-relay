// Initials avatar with an optional admin crown. Name renders as text (escaped).
//
// The background colour is derived from the name so people are distinguishable
// at a glance; every avatar being the same blue made conversations hard to
// scan. The palette is hand-picked rather than hashed to an arbitrary hue that
// might clash or wash out.
//
// The initials are dark, not white. These are mid-tone colours, and white on
// them measured between 2.9:1 and 3.6:1, under the 4.5:1 minimum for text.
// Dark ink clears it on every swatch while leaving the colours as vivid as
// they were, which is the point of having a palette at all. Three swatches
// were lightened slightly to pass; the rest are untouched.
const PALETTE = [
  "#e0567b", "#e0713a", "#d9a441", "#4bab5a", "#3aa6a0",
  "#4b8ff0", "#7d6eff", "#ac68dd", "#d356a8", "#5f97b0",
  "#d2705a", "#7a9c3e",
];

function colorFor(name) {
  let hash = 0;
  const s = name || "?";
  for (let i = 0; i < s.length; i++) {
    hash = (hash * 31 + s.charCodeAt(i)) | 0;
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

export default function Avatar({ name, admin = false, size = "sm" }) {
  return (
    <span className="avatar-holder">
      <span
        className={`avatar ${size}`}
        style={{ background: colorFor(name) }}
      >
        {(name || "?")[0]?.toUpperCase()}
      </span>
      {admin && (
        <span className="crown" title="Admin" aria-label="Admin">
          👑
        </span>
      )}
    </span>
  );
}
