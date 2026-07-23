// Initials avatar with an optional admin crown. Name renders as text (escaped).
export default function Avatar({ name, admin = false, size = "sm" }) {
  return (
    <span className="avatar-holder">
      <span className={`avatar ${size}`}>
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
