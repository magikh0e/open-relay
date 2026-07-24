// Initials avatar with an optional admin crown. Name renders as text (escaped).
//
// The background colour is derived from the name so people are distinguishable
// at a glance — every avatar being the same blue made conversations hard to
// scan. The palette is hand-picked to sit well on the dark theme with white
// initials, rather than hashing to an arbitrary hue that might clash or wash out.
const PALETTE = [
  "#e0567b", "#e0713a", "#d9a441", "#4bab5a", "#3aa6a0",
  "#4b8ff0", "#6a5cff", "#a259d6", "#d356a8", "#5f97b0",
  "#c0553f", "#7a9c3e",
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
