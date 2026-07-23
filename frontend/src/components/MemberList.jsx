import Avatar from "./Avatar.jsx";

// Right-hand panel: channel members split into Online / Offline using the live
// presence set. Moderators see kick/ban actions on other members.
export default function MemberList({
  members,
  online,
  awayMap = {},
  onOpenProfile,
  canModerate,
  canManageRoles,
  myId,
  onKick,
  onBan,
  onSetRole,
  bind,
}) {
  const onlineMembers = members.filter((m) => online.has(m.id));
  const offlineMembers = members.filter((m) => !online.has(m.id));

  const shared = {
    online,
    awayMap,
    onOpenProfile,
    canModerate,
    canManageRoles,
    myId,
    onKick,
    onBan,
    onSetRole,
  };

  return (
    <aside className="member-list" {...bind}>
      <Section
        title={`Online — ${onlineMembers.length}`}
        members={onlineMembers}
        isOnline
        {...shared}
      />
      {offlineMembers.length > 0 && (
        <Section
          title={`Offline — ${offlineMembers.length}`}
          members={offlineMembers}
          {...shared}
        />
      )}
    </aside>
  );
}

function Section({
  title,
  members,
  isOnline,
  awayMap = {},
  onOpenProfile,
  canModerate,
  canManageRoles,
  myId,
  onKick,
  onBan,
  onSetRole,
}) {
  return (
    <div className="section">
      <div className="section-head">
        <span>{title}</span>
      </div>
      {members.map((m) => {
        // Can't act on yourself or the channel owner.
        const targetable = m.id !== myId && m.role !== "owner";
        const showRole = canManageRoles && targetable;
        const showMod = canModerate && targetable;
        const isOp = m.role === "mod";
        return (
          <div key={m.id} className={`member-row ${isOnline ? "" : "off"}`}>
            <button
              className="member-main"
              onClick={() => onOpenProfile?.(m.id)}
              title={
                awayMap[m.id]
                  ? `@${m.username} — away: ${awayMap[m.id]}`
                  : `@${m.username}`
              }
            >
              <span className="member-avatar-wrap">
                <Avatar name={m.display_name} admin={m.is_admin} />
                {isOnline && (
                  <span
                    className={`presence-dot ${awayMap[m.id] ? "away" : ""}`}
                  />
                )}
              </span>
              <span className={`member-name ${awayMap[m.id] ? "is-away" : ""}`}>
                {m.display_name}
              </span>
              {m.role === "owner" && <span className="role-tag">owner</span>}
              {isOp && <span className="role-tag op">op</span>}
            </button>
            {(showRole || showMod) && (
              <span className="member-actions">
                {showRole && (
                  <button
                    className={`act ${isOp ? "op-on" : ""}`}
                    title={
                      isOp
                        ? `Remove operator from ${m.display_name}`
                        : `Make ${m.display_name} an operator`
                    }
                    onClick={() => onSetRole(m, isOp ? "member" : "mod")}
                  >
                    {isOp ? "★" : "☆"}
                  </button>
                )}
                {showMod && (
                  <>
                    <button
                      className="act"
                      title={`Kick ${m.display_name}`}
                      onClick={() => onKick(m)}
                    >
                      ✖
                    </button>
                    <button
                      className="act danger"
                      title={`Ban ${m.display_name}`}
                      onClick={() => onBan(m)}
                    >
                      ⛔
                    </button>
                  </>
                )}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
