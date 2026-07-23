import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { useSocket } from "../useSocket.js";
import Sidebar from "./Sidebar.jsx";
import MessagePane from "./MessagePane.jsx";
import MemberList from "./MemberList.jsx";
import Profile from "./Profile.jsx";
import ChannelSettings from "./ChannelSettings.jsx";

// Reconcile a reaction delta ({emoji, count, user_id, added}) into a message's
// reaction summary list. Counts come authoritatively from the server; we only
// derive our own "me" flag from whether the acting user_id is us.
function applyReaction(reactions, data, myId) {
  const { emoji, count, user_id, added } = data;
  const mine = user_id === myId;
  const idx = reactions.findIndex((r) => r.emoji === emoji);
  if (count <= 0) return reactions.filter((r) => r.emoji !== emoji);
  if (idx === -1) {
    return [...reactions, { emoji, count, me: mine && added }];
  }
  return reactions.map((r) =>
    r.emoji === emoji
      ? { ...r, count, me: mine ? added : r.me }
      : r
  );
}

export default function ChatShell() {
  const { user } = useAuth();
  const [channels, setChannels] = useState([]);
  const [dms, setDms] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [online, setOnline] = useState(new Set());
  const [typing, setTyping] = useState({}); // channelId -> {userId: expiresAt}
  const [profileUserId, setProfileUserId] = useState(null);
  const [membersByChannel, setMembersByChannel] = useState({});
  const [settingsOpen, setSettingsOpen] = useState(false);

  // Messages cached per channel so switching back is instant.
  const [msgsByChannel, setMsgsByChannel] = useState({});
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;

  const refreshLists = useCallback(async () => {
    const [chs, dmList, on] = await Promise.all([
      api("/channels"),
      api("/dms"),
      api("/users/online"),
    ]);
    setChannels(chs);
    setDms(dmList);
    setOnline(new Set(on));
  }, []);

  useEffect(() => {
    refreshLists();
  }, [refreshLists]);

  // --- live event handling -------------------------------------------------
  const onEvent = useCallback((msg) => {
    const { type, data } = msg;
    if (type === "message") {
      setMsgsByChannel((prev) => {
        const list = prev[data.channel_id] || [];
        if (list.some((m) => m.id === data.id)) return prev; // dedupe echo
        return { ...prev, [data.channel_id]: [...list, data] };
      });
    } else if (type === "message_deleted") {
      setMsgsByChannel((prev) => {
        const next = { ...prev };
        for (const cid of Object.keys(next)) {
          next[cid] = next[cid].filter((m) => m.id !== data.id);
        }
        return next;
      });
    } else if (type === "message_edited") {
      setMsgsByChannel((prev) => {
        const list = prev[data.channel_id];
        if (!list) return prev;
        return {
          ...prev,
          [data.channel_id]: list.map((m) =>
            m.id === data.id
              ? {
                  ...m,
                  content: data.content,
                  edited_at: data.edited_at,
                  mentions: data.mentions ?? m.mentions,
                }
              : m
          ),
        };
      });
    } else if (type === "reaction") {
      setMsgsByChannel((prev) => {
        const list = prev[data.channel_id];
        if (!list) return prev;
        return {
          ...prev,
          [data.channel_id]: list.map((m) =>
            m.id === data.message_id
              ? { ...m, reactions: applyReaction(m.reactions || [], data, user.id) }
              : m
          ),
        };
      });
    } else if (type === "presence") {
      setOnline((prev) => {
        const next = new Set(prev);
        data.online ? next.add(data.user_id) : next.delete(data.user_id);
        return next;
      });
    } else if (type === "typing") {
      if (data.user_id === user.id) return;
      setTyping((prev) => ({
        ...prev,
        [data.channel_id]: {
          ...(prev[data.channel_id] || {}),
          [data.user_id]: Date.now() + 4000,
        },
      }));
    } else if (type === "dm_opened") {
      refreshLists();
    } else if (type === "member_removed" || type === "member_updated") {
      // Roster changed (kick/ban or role change) — refresh it.
      const cid = data.channel_id;
      api(`/channels/${cid}/members`)
        .then((rows) =>
          setMembersByChannel((prev) => ({ ...prev, [cid]: rows }))
        )
        .catch(() => {});
    } else if (type === "channel_kicked") {
      // I was removed from a channel — drop it and leave the view if it's open.
      const cid = data.channel_id;
      setActiveId((prev) => (prev === cid ? null : prev));
      setMembersByChannel((prev) => {
        const next = { ...prev };
        delete next[cid];
        return next;
      });
      refreshLists();
    } else if (type === "channel_updated") {
      setChannels((prev) =>
        prev.map((c) =>
          c.id === data.channel_id
            ? {
                ...c,
                name: data.name,
                topic: data.topic,
                kind: data.kind ?? c.kind,
              }
            : c
        )
      );
    } else if (type === "channel_deleted") {
      // A channel was deleted — drop all trace of it.
      const cid = data.channel_id;
      setActiveId((prev) => (prev === cid ? null : prev));
      setMembersByChannel((prev) => {
        const next = { ...prev };
        delete next[cid];
        return next;
      });
      setMsgsByChannel((prev) => {
        const next = { ...prev };
        delete next[cid];
        return next;
      });
      refreshLists();
    }
  }, [user.id, refreshLists]);

  const { send } = useSocket(true, onEvent);

  // Expire stale typing indicators.
  useEffect(() => {
    const t = setInterval(() => {
      setTyping((prev) => {
        const now = Date.now();
        const next = {};
        for (const [cid, users] of Object.entries(prev)) {
          const kept = Object.fromEntries(
            Object.entries(users).filter(([, exp]) => exp > now)
          );
          if (Object.keys(kept).length) next[cid] = kept;
        }
        return next;
      });
    }, 1000);
    return () => clearInterval(t);
  }, []);

  // Load history when a channel becomes active (once).
  useEffect(() => {
    if (!activeId || msgsByChannel[activeId]) return;
    api(`/channels/${activeId}/messages`).then((rows) =>
      setMsgsByChannel((prev) => ({ ...prev, [activeId]: rows }))
    );
  }, [activeId, msgsByChannel]);

  // Load the member roster whenever a channel becomes active (kept fresh on
  // each open); presence is layered on client-side from the `online` set.
  useEffect(() => {
    if (!activeId) return;
    api(`/channels/${activeId}/members`)
      .then((rows) =>
        setMembersByChannel((prev) => ({ ...prev, [activeId]: rows }))
      )
      .catch(() => {});
  }, [activeId]);

  async function openChannel(id) {
    setActiveId(id);
    setSettingsOpen(false);
    send({ type: "subscribe", channel_id: id }); // ensure live delivery
  }

  async function joinAndOpen(channel) {
    await api(`/channels/${channel.id}/join`, { method: "POST" });
    await refreshLists();
    openChannel(channel.id);
  }

  async function moderate(action, channelId, member) {
    const verb = action === "ban" ? "Ban" : "Kick";
    const extra =
      action === "ban" ? " They won't be able to rejoin." : "";
    if (!window.confirm(`${verb} ${member.display_name}?${extra}`)) return;
    try {
      await api(`/channels/${channelId}/${action}`, {
        method: "POST",
        body: { user_id: member.id },
      });
      const rows = await api(`/channels/${channelId}/members`);
      setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    } catch (e) {
      window.alert(e.message);
    }
  }

  const active =
    channels.find((c) => c.id === activeId) ||
    dms.find((c) => c.id === activeId) ||
    null;

  const activeMembers = (active && membersByChannel[active.id]) || [];
  const myRole = activeMembers.find((m) => m.id === user.id)?.role;
  // Owner detection falls back to `created_by` from /channels so it doesn't
  // depend on the members endpoint returning `role`.
  const isOwner =
    myRole === "owner" || (!!active && active.created_by === user.id);
  const canModerate = user.is_admin || isOwner || myRole === "mod";
  const canDelete = user.is_admin || isOwner;
  const canManageRoles = user.is_admin || isOwner;

  async function setRole(channelId, member, role) {
    try {
      await api(`/channels/${channelId}/role`, {
        method: "POST",
        body: { user_id: member.id, role },
      });
      const rows = await api(`/channels/${channelId}/members`);
      setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    } catch (e) {
      window.alert(e.message);
    }
  }

  async function updateChannel(channelId, patch) {
    const updated = await api(`/channels/${channelId}`, {
      method: "PATCH",
      body: patch,
    });
    setChannels((prev) =>
      prev.map((c) =>
        c.id === channelId
          ? { ...c, name: updated.name, topic: updated.topic, kind: updated.kind }
          : c
      )
    );
  }

  async function deleteChannel(channel) {
    if (
      !window.confirm(
        `Delete #${channel.name}? This permanently removes the channel and all its messages for everyone.`
      )
    )
      return;
    try {
      await api(`/channels/${channel.id}`, { method: "DELETE" });
      setActiveId((prev) => (prev === channel.id ? null : prev));
      await refreshLists();
    } catch (e) {
      window.alert(e.message);
    }
  }

  return (
    <div className="shell">
      <Sidebar
        channels={channels}
        dms={dms}
        activeId={activeId}
        online={online}
        onOpen={openChannel}
        onJoin={joinAndOpen}
        onRefresh={refreshLists}
        onOpenProfile={setProfileUserId}
      />
      <div className="main-area">
        {active ? (
          <MessagePane
            key={active.id}
            channel={active}
            messages={msgsByChannel[active.id] || []}
            typing={typing[active.id] || {}}
            online={online}
            onSent={(m) =>
              setMsgsByChannel((prev) => {
                const list = prev[active.id] || [];
                if (list.some((x) => x.id === m.id)) return prev;
                return { ...prev, [active.id]: [...list, m] };
              })
            }
            onTyping={() => send({ type: "typing", channel_id: active.id })}
            onOpenProfile={setProfileUserId}
            canDelete={canDelete}
            onDeleteChannel={() => deleteChannel(active)}
            canManage={canDelete}
            onSetTopic={(topic) => updateChannel(active.id, { topic })}
            onOpenSettings={() => setSettingsOpen(true)}
          />
        ) : (
          <div className="center muted pane">Pick a channel to start chatting</div>
        )}

        {active && active.kind !== "dm" && (
          <MemberList
            members={activeMembers}
            online={online}
            onOpenProfile={setProfileUserId}
            canModerate={canModerate}
            canManageRoles={canManageRoles}
            myId={user.id}
            onKick={(m) => moderate("kick", active.id, m)}
            onBan={(m) => moderate("ban", active.id, m)}
            onSetRole={(m, role) => setRole(active.id, m, role)}
          />
        )}
      </div>

      {profileUserId && (
        <Profile
          userId={profileUserId}
          onClose={() => setProfileUserId(null)}
        />
      )}

      {settingsOpen && active && active.kind !== "dm" && (
        <ChannelSettings
          channel={active}
          members={activeMembers}
          myId={user.id}
          onUpdate={(patch) => updateChannel(active.id, patch)}
          onSetRole={(m, role) => setRole(active.id, m, role)}
          onKick={(m) => moderate("kick", active.id, m)}
          onBan={(m) => moderate("ban", active.id, m)}
          onDelete={() => {
            setSettingsOpen(false);
            deleteChannel(active);
          }}
          onOpenProfile={setProfileUserId}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
