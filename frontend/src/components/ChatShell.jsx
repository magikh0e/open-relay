import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { useSocket } from "../useSocket.js";
import Sidebar from "./Sidebar.jsx";
import MessagePane from "./MessagePane.jsx";
import MemberList from "./MemberList.jsx";
import Profile from "./Profile.jsx";
import ChannelSettings from "./ChannelSettings.jsx";
import ThreadPane from "./ThreadPane.jsx";
import SearchModal from "./SearchModal.jsx";
import { APP_VERSION } from "../version.js";

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
  const { user, updateUser, logout } = useAuth();
  const [ignored, setIgnored] = useState(() => {
    try {
      return new Set(JSON.parse(localStorage.getItem("relay_ignored") || "[]"));
    } catch {
      return new Set();
    }
  });
  const [channels, setChannels] = useState([]);
  const [dms, setDms] = useState([]);
  const [activeId, setActiveId] = useState(null);
  const [online, setOnline] = useState(new Set());
  const [awayMap, setAwayMap] = useState({}); // userId -> away message
  const [typing, setTyping] = useState({}); // channelId -> {userId: expiresAt}
  const [profileUserId, setProfileUserId] = useState(null);
  const [membersByChannel, setMembersByChannel] = useState({});
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [threadRootId, setThreadRootId] = useState(null);
  const [threadMessages, setThreadMessages] = useState([]);

  // Messages cached per channel so switching back is instant.
  const [msgsByChannel, setMsgsByChannel] = useState({});
  const activeIdRef = useRef(activeId);
  activeIdRef.current = activeId;
  const threadRootIdRef = useRef(threadRootId);
  threadRootIdRef.current = threadRootId;

  const refreshLists = useCallback(async () => {
    const [chs, dmList, on, aw] = await Promise.all([
      api("/channels"),
      api("/dms"),
      api("/users/online"),
      api("/users/away"),
    ]);
    setChannels(chs);
    setDms(dmList);
    setOnline(new Set(on));
    setAwayMap(aw || {});
  }, []);

  useEffect(() => {
    refreshLists();
  }, [refreshLists]);

  // --- live event handling -------------------------------------------------
  const onEvent = useCallback((msg) => {
    const { type, data } = msg;
    if (type === "message") {
      if (data.thread_root_id) {
        // Thread reply: bump the root's count in the main timeline, and append
        // to the open thread — never show it in the main channel list.
        setMsgsByChannel((prev) => {
          const list = prev[data.channel_id];
          if (!list) return prev;
          return {
            ...prev,
            [data.channel_id]: list.map((m) =>
              m.id === data.thread_root_id
                ? {
                    ...m,
                    reply_count: (m.reply_count || 0) + 1,
                    last_reply_at: data.created_at,
                  }
                : m
            ),
          };
        });
        if (threadRootIdRef.current === data.thread_root_id) {
          setThreadMessages((prev) =>
            prev.some((m) => m.id === data.id) ? prev : [...prev, data]
          );
        }
      } else {
        setMsgsByChannel((prev) => {
          const list = prev[data.channel_id] || [];
          if (list.some((m) => m.id === data.id)) return prev; // dedupe echo
          return { ...prev, [data.channel_id]: [...list, data] };
        });
      }
    } else if (type === "message_deleted") {
      setMsgsByChannel((prev) => {
        const next = { ...prev };
        for (const cid of Object.keys(next)) {
          next[cid] = next[cid].filter((m) => m.id !== data.id);
        }
        return next;
      });
      setThreadMessages((prev) => prev.filter((m) => m.id !== data.id));
    } else if (type === "message_edited") {
      const patch = (m) =>
        m.id === data.id
          ? {
              ...m,
              content: data.content,
              edited_at: data.edited_at,
              mentions: data.mentions ?? m.mentions,
            }
          : m;
      setMsgsByChannel((prev) => {
        const list = prev[data.channel_id];
        if (!list) return prev;
        return { ...prev, [data.channel_id]: list.map(patch) };
      });
      setThreadMessages((prev) => prev.map(patch));
    } else if (type === "reaction") {
      const patch = (m) =>
        m.id === data.message_id
          ? { ...m, reactions: applyReaction(m.reactions || [], data, user.id) }
          : m;
      setMsgsByChannel((prev) => {
        const list = prev[data.channel_id];
        if (!list) return prev;
        return { ...prev, [data.channel_id]: list.map(patch) };
      });
      setThreadMessages((prev) => prev.map(patch));
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
    } else if (type === "away") {
      setAwayMap((prev) => {
        const next = { ...prev };
        if (data.away) next[data.user_id] = data.message || "away";
        else delete next[data.user_id];
        return next;
      });
    } else if (type === "dm_opened" || type === "channel_added") {
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
    closeThread();
    send({ type: "subscribe", channel_id: id }); // ensure live delivery
  }

  async function openThread(m) {
    const rootId = m.thread_root_id || m.id;
    setThreadRootId(rootId);
    setThreadMessages([]);
    try {
      const rows = await api(
        `/channels/${m.channel_id}/messages/${rootId}/thread`
      );
      setThreadMessages(rows);
    } catch {
      setThreadRootId(null);
    }
  }

  function closeThread() {
    setThreadRootId(null);
    setThreadMessages([]);
  }

  async function openDM(userId) {
    try {
      const dm = await api("/dms", { method: "POST", body: { user_id: userId } });
      setProfileUserId(null);
      await refreshLists(); // ensure the DM shows in the sidebar
      openChannel(dm.id);
    } catch (e) {
      window.alert(e.message);
    }
  }

  // IRC-style slash commands. Returns {ok, message} for feedback, or throws
  // Error (message surfaced to the user). Permission is enforced server-side.
  async function runCommand(raw) {
    if (!active) throw new Error("Open a channel first");
    const text = raw.slice(1).trim();
    const sp = text.indexOf(" ");
    const cmd = (sp === -1 ? text : text.slice(0, sp)).toLowerCase();
    const argStr = sp === -1 ? "" : text.slice(sp + 1).trim();
    const args = argStr ? argStr.split(/\s+/) : [];

    async function resolveUser(uname) {
      const u = (uname || "").replace(/^@/, "");
      if (!u) throw new Error("Usage: that command needs a username");
      const m = active && (membersByChannel[active.id] || []).find(
        (x) => x.username.toLowerCase() === u.toLowerCase()
      );
      if (m) return m;
      const res = await api(`/users/search?q=${encodeURIComponent(u)}`).catch(
        () => []
      );
      const hit = res.find((x) => x.username.toLowerCase() === u.toLowerCase());
      if (!hit) throw new Error(`No user named "${u}"`);
      return hit;
    }

    const post = (path, body) =>
      api(`/channels/${active.id}${path}`, { method: "POST", body });

    switch (cmd) {
      case "help":
        return {
          ok: true,
          message:
            "/me · /nick <name> · /join <#chan> · /part · /invite <user> · /query <user> · /whois <user> · /names · /away [msg] · /back · /ignore <user> · /clear · /quit · /topic · /kick · /ban · /unban · /op · /deop · /dm · /slap · /shrug · /version",
        };
      case "version":
      case "health": {
        let server = "?";
        try {
          const h = await api("/health", { auth: false });
          server = h?.version || "?";
        } catch {
          server = "unreachable";
        }
        return {
          ok: true,
          message: `Relay — client v${APP_VERSION} · server v${server}`,
        };
      }
      case "me": {
        if (!argStr) throw new Error("Usage: /me <action>");
        await post("/messages", { content: `/me ${argStr}` });
        return { ok: true };
      }
      case "nick": {
        if (!argStr) throw new Error("Usage: /nick <display name>");
        const updated = await api("/users/me", {
          method: "PATCH",
          body: { display_name: argStr },
        });
        updateUser({ display_name: updated.display_name });
        return { ok: true, message: `Display name set to ${updated.display_name}` };
      }
      case "join": {
        const slug = (args[0] || "").replace(/^#/, "").toLowerCase();
        if (!slug) throw new Error("Usage: /join <#channel>");
        const ch = channels.find((c) => (c.slug || "").toLowerCase() === slug);
        if (!ch) throw new Error(`No channel #${slug}`);
        if (!ch.is_member) {
          await api(`/channels/${ch.id}/join`, { method: "POST" });
          await refreshLists();
        }
        openChannel(ch.id);
        return { ok: true, message: `Joined #${ch.name}` };
      }
      case "part":
      case "leave": {
        if (active.kind === "dm")
          throw new Error("Can't /part a direct message");
        await api(`/channels/${active.id}/leave`, { method: "POST" });
        setActiveId(null);
        await refreshLists();
        return { ok: true, message: `Left #${active.name}` };
      }
      case "query": {
        const u = await resolveUser(args[0]);
        await openDM(u.id);
        return { ok: true, message: `Opened DM with ${u.display_name}` };
      }
      case "whois": {
        const u = await resolveUser(args[0]);
        setProfileUserId(u.id);
        return { ok: true, message: `Opening ${u.display_name}'s profile…` };
      }
      case "names": {
        const members = active ? membersByChannel[active.id] || [] : [];
        if (!members.length) return { ok: true, message: "No members here." };
        return {
          ok: true,
          message: `${members.length}: ${members
            .map((m) => m.display_name)
            .join(", ")}`,
        };
      }
      case "ignore": {
        const u = await resolveUser(args[0]);
        const isIgnored = ignored.has(u.id);
        setIgnored((prev) => {
          const next = new Set(prev);
          isIgnored ? next.delete(u.id) : next.add(u.id);
          localStorage.setItem("relay_ignored", JSON.stringify([...next]));
          return next;
        });
        return {
          ok: true,
          message: isIgnored
            ? `No longer ignoring ${u.display_name}`
            : `Ignoring ${u.display_name} (hiding their messages)`,
        };
      }
      case "clear":
        setMsgsByChannel((prev) => ({ ...prev, [active.id]: [] }));
        return {
          ok: true,
          message: "Cleared this view locally — reload to restore history.",
        };
      case "quit":
        logout();
        return { ok: true };
      case "topic":
        await updateChannel(active.id, { topic: argStr });
        return { ok: true, message: argStr ? "Topic set." : "Topic cleared." };
      case "kick": {
        const u = await resolveUser(args[0]);
        await post("/kick", { user_id: u.id, reason: args.slice(1).join(" ") });
        return { ok: true, message: `Kicked ${u.display_name}.` };
      }
      case "ban": {
        const u = await resolveUser(args[0]);
        await post("/ban", { user_id: u.id, reason: args.slice(1).join(" ") });
        return { ok: true, message: `Banned ${u.display_name}.` };
      }
      case "unban": {
        const u = await resolveUser(args[0]);
        await post("/unban", { user_id: u.id });
        return { ok: true, message: `Unbanned ${u.display_name}.` };
      }
      case "op": {
        const u = await resolveUser(args[0]);
        await post("/role", { user_id: u.id, role: "mod" });
        return { ok: true, message: `${u.display_name} is now an operator.` };
      }
      case "deop": {
        const u = await resolveUser(args[0]);
        await post("/role", { user_id: u.id, role: "member" });
        return { ok: true, message: `${u.display_name} is no longer an operator.` };
      }
      case "dm":
      case "msg": {
        const u = await resolveUser(args[0]);
        await openDM(u.id);
        return { ok: true, message: `Opened DM with ${u.display_name}.` };
      }
      case "slap": {
        const u = await resolveUser(args[0]);
        await post("/messages", {
          content: `slaps @${u.username} around a bit with a large brown trout 🐟`,
        });
        return { ok: true };
      }
      case "shrug": {
        const content = (argStr ? argStr + " " : "") + "¯\\_(ツ)_/¯";
        await post("/messages", { content });
        return { ok: true };
      }
      case "invite": {
        const u = await resolveUser(args[0]);
        await post("/invite", { user_id: u.id });
        return { ok: true, message: `Invited ${u.display_name} to the channel` };
      }
      case "away": {
        await api("/users/away", {
          method: "POST",
          body: { message: argStr },
        });
        return {
          ok: true,
          message: argStr ? `You're now away: ${argStr}` : "You're back.",
        };
      }
      case "back": {
        await api("/users/away", { method: "POST", body: { message: "" } });
        return { ok: true, message: "Welcome back." };
      }
      default:
        throw new Error(`Unknown command: /${cmd} — try /help`);
    }
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
        onOpenSearch={() => setSearchOpen(true)}
      />
      <div className="main-area">
        {active ? (
          <MessagePane
            key={active.id}
            channel={active}
            messages={(msgsByChannel[active.id] || []).filter(
              (m) => !ignored.has(m.sender_id)
            )}
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
            onOpenThread={openThread}
            onCommand={runCommand}
          />
        ) : (
          <div className="center muted pane">Pick a channel to start chatting</div>
        )}

        {threadRootId && active ? (
          <ThreadPane
            channel={active}
            messages={threadMessages}
            onClose={closeThread}
            onOpenProfile={setProfileUserId}
          />
        ) : active && active.kind !== "dm" ? (
          <MemberList
            members={activeMembers}
            online={online}
            awayMap={awayMap}
            onOpenProfile={setProfileUserId}
            canModerate={canModerate}
            canManageRoles={canManageRoles}
            myId={user.id}
            onKick={(m) => moderate("kick", active.id, m)}
            onBan={(m) => moderate("ban", active.id, m)}
            onSetRole={(m, role) => setRole(active.id, m, role)}
          />
        ) : null}
      </div>

      {profileUserId && (
        <Profile
          userId={profileUserId}
          onClose={() => setProfileUserId(null)}
          onMessage={openDM}
        />
      )}

      {searchOpen && (
        <SearchModal
          onClose={() => setSearchOpen(false)}
          onOpen={(channelId) => {
            setSearchOpen(false);
            openChannel(channelId);
          }}
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
