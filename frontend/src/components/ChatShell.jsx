import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api.js";
import { useAuth } from "../auth.jsx";
import { useSocket } from "../useSocket.js";
import { useSwipe } from "../useSwipe.js";
import Sidebar from "./Sidebar.jsx";
import MessagePane from "./MessagePane.jsx";
import MemberList from "./MemberList.jsx";
import Profile from "./Profile.jsx";
import ChannelSettings from "./ChannelSettings.jsx";
import GroupInfo from "./GroupInfo.jsx";
import ThreadPane from "./ThreadPane.jsx";
import SearchModal from "./SearchModal.jsx";
import E2EESetup from "./E2EESetup.jsx";
import ConfirmDialog from "./ConfirmDialog.jsx";
import {
  decryptMessage,
  deriveSharedKey,
  encryptMessage,
  generateGroupKey,
  wrapGroupKey,
  unwrapGroupKey,
  exportPublicKey,
  importPublicKey,
  loadCachedKey,
  safetyNumber,
} from "../e2ee.js";
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
  const [rosterOpen, setRosterOpen] = useState(false); // mobile roster drawer
  const [dialog, setDialog] = useState(null); // in-app confirm/alert

  // Promise-based stand-ins for window.confirm / window.alert so call sites
  // stay readable while the UI stays in the app's own visual language.
  function ask(opts) {
    return new Promise((resolve) => setDialog({ ...opts, resolve }));
  }
  function notify(message, title = "Something went wrong") {
    return ask({ title, body: message, alertOnly: true });
  }

  // --- DM end-to-end encryption ---
  const [privateKey, setPrivateKey] = useState(null);
  // loading | none (never set up) | locked (bundle exists, key not unwrapped) | unlocked
  const [keyStatus, setKeyStatus] = useState("loading");
  const [e2eeModal, setE2eeModal] = useState(null); // "setup" | "unlock" | null
  const [sharedKeys, setSharedKeys] = useState({}); // channelId -> AES key
  // channelId -> { current: epoch|null, byEpoch: {epoch: AES key} }
  const [groupKeys, setGroupKeys] = useState({});
  const [keyEpoch, setKeyEpoch] = useState(0); // bumped to force re-derivation
  const [fingerprints, setFingerprints] = useState({}); // channelId -> safety number
  const [jumpTo, setJumpTo] = useState(null); // message id to scroll to on open
  const [decrypted, setDecrypted] = useState({}); // messageId -> plaintext | null
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
        // Update unread badges for channels the user isn't currently looking at.
        if (data.channel_id !== activeIdRef.current) refreshLists();
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
    } else if (type === "keys_published") {
      // The other person just enabled encryption. Drop any cached (or absent)
      // shared key for that DM and nudge the derive effect so the conversation
      // upgrades to encrypted without needing to be reopened.
      setSharedKeys((prev) => {
        const next = { ...prev };
        delete next[data.channel_id];
        return next;
      });
      setKeyEpoch((n) => n + 1);
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
                has_password: data.has_password ?? c.has_password,
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

  // Restore the unlocked key from this tab's session, or find out whether the
  // user has a key bundle at all.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const cached = await loadCachedKey();
      if (cancelled) return;
      if (cached) {
        setPrivateKey(cached);
        setKeyStatus("unlocked");
        return;
      }
      try {
        await api("/keys/me");
        if (!cancelled) setKeyStatus("locked");
      } catch {
        if (!cancelled) setKeyStatus("none");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function openChannel(id, jumpToMessageId = null) {
    if (jumpToMessageId) {
      // Load a window of context around the target rather than the newest
      // page, so the message is actually on screen when we arrive.
      try {
        const rows = await api(
          `/channels/${id}/messages?around=${jumpToMessageId}&limit=50`
        );
        setMsgsByChannel((prev) => ({ ...prev, [id]: rows }));
      } catch {
        /* fall back to normal history */
      }
      setJumpTo(jumpToMessageId);
    }
    setActiveId(id);
    setSettingsOpen(false);
    setRosterOpen(false);
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

  // Opening a channel (or receiving while it's open) clears its badge.
  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    (async () => {
      try {
        await api(`/channels/${activeId}/read`, { method: "POST" });
        if (!cancelled) refreshLists();
      } catch {
        /* badge will catch up on the next refresh */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [activeId, msgsByChannel[activeId]?.length, refreshLists]);

  // Land in #whatsnew on sign-in so release notes are the first thing seen.
  // Guarded by a ref so it only happens once — re-renders (or the user closing
  // the channel) must not yank them back here.
  const autoOpenedRef = useRef(false);
  useEffect(() => {
    if (autoOpenedRef.current || activeId || !channels.length) return;
    // On mobile the sidebar IS the home screen — auto-opening a channel would
    // mean pressing back on every launch. Desktop shows both at once, so
    // landing in #whatsnew there costs nothing.
    if (window.matchMedia("(max-width: 768px)").matches) return;
    const wn = channels.find((c) => c.slug === "whatsnew");
    if (!wn) return;
    autoOpenedRef.current = true;
    openChannel(wn.id);
  }, [channels, activeId]);

  // Closing a DM only hides it for you — nothing is deleted, and it comes back
  // if either of you sends a new message.
  async function closeDM(channel) {
    try {
      await api(`/dms/${channel.id}`, { method: "DELETE" });
      setActiveId((prev) => (prev === channel.id ? null : prev));
      await refreshLists();
    } catch (e) {
      notify(e.message, "Couldn't close that conversation");
    }
  }

  async function openDM(userId) {
    try {
      const dm = await api("/dms", { method: "POST", body: { user_id: userId } });
      setProfileUserId(null);
      await refreshLists(); // ensure the DM shows in the sidebar
      openChannel(dm.id);
    } catch (e) {
      notify(e.message, "Couldn't open that conversation");
    }
  }

  async function createGroup(userIds, name) {
    // Returns the new group; the caller (the New-conversation modal) refreshes
    // the sidebar and opens it.
    return api("/dms/group", {
      method: "POST",
      body: { user_ids: userIds, name: name || undefined },
    });
  }

  async function addGroupMember(channelId, userId) {
    await api(`/dms/${channelId}/members`, {
      method: "POST",
      body: { user_id: userId },
    });
    const rows = await api(`/channels/${channelId}/members`);
    setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    // A new member gets a new epoch rather than the current key, so the
    // backlog stays closed to them. Until this lands the server refuses
    // encrypted sends, so a failure here stalls the group rather than
    // quietly letting them read.
    if (groupKeys[channelId]?.current) await rekeyGroup(channelId);
  }

  async function removeGroupMember(channelId, userId) {
    await api(`/dms/${channelId}/members/${userId}`, { method: "DELETE" });
    const rows = await api(`/channels/${channelId}/members`);
    setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    // Rotating is what makes the removal real: they keep the old key, but it
    // opens nothing said from here on.
    if (groupKeys[channelId]?.current) await rekeyGroup(channelId);
  }

  async function leaveGroup(channel) {
    const ok = await ask({
      title: `Leave ${channel.name}?`,
      body: "You'll stop receiving its messages and need to be added back to rejoin.",
      confirmLabel: "Leave group",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/channels/${channel.id}/leave`, { method: "POST" });
      setActiveId((prev) => (prev === channel.id ? null : prev));
      await refreshLists();
    } catch (e) {
      notify(e.message, "Couldn't leave that group");
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
            "/me · /nick <name> · /join <#chan> · /part · /close (hide a DM) · /invite <user> · /query <user> · /whois <user> · /names · /away [msg] · /back · /ignore <user> · /clear · /quit · /topic · /kick · /ban · /unban · /op · /deop · /dm · /slap · /shrug · /version · /mode [+|-][o|b|k|i] (IRC-style: op, ban, key, invite-only)",
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
        // The desktop shell injects its own version; show it (distinct from the
        // bundled web version) so desktop users see the number they update.
        const desktop =
          typeof window !== "undefined" && window.__RELAY_DESKTOP_VERSION__;
        const client = desktop
          ? `desktop v${desktop} · web v${APP_VERSION}`
          : `client v${APP_VERSION}`;
        return {
          ok: true,
          message: `Open Relay: ${client} · server v${server}`,
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
      case "join":
      case "j": {
        const slug = (args[0] || "").replace(/^#/, "").toLowerCase();
        if (!slug) throw new Error("Usage: /join <#channel> [password]");
        const ch = channels.find((c) => (c.slug || "").toLowerCase() === slug);
        if (!ch) throw new Error(`No channel #${slug}`);
        if (!ch.is_member) {
          // Second arg is the channel key for password-protected channels.
          const opts = { method: "POST" };
          if (args[1]) opts.body = { password: args[1] };
          await api(`/channels/${ch.id}/join`, opts);
          await refreshLists();
        }
        openChannel(ch.id);
        return { ok: true, message: `Joined #${ch.name}` };
      }
      case "close":
      case "part":
      case "leave": {
        // In a DM there's nothing to leave — close it (hides it for you only).
        if (active.kind === "dm") {
          const who = active.name;
          await closeDM(active);
          return {
            ok: true,
            message: `Closed your DM with ${who}. It'll reappear if either of you writes again.`,
          };
        }
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
          message: "Cleared this view locally; reload to restore history.",
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
      case "mode": {
        // IRC-style channel modes, mapped onto the app's own actions. Acts on
        // the open channel. Supported flags:
        //   +o/-o <user>  operator (same as /op, /deop)
        //   +b/-b <user>  ban / unban
        //   +k <key> / -k channel password (set / clear)
        //   +i/-i         invite-only (private) / public
        if (!active || active.kind === "dm" || active.kind === "group") {
          throw new Error("/mode works in a channel.");
        }
        // Tolerate a leading "#channel" token (IRC habit); we always act on
        // the channel that's open.
        let rest = args[0]?.startsWith("#") ? args.slice(1) : args;
        const m = /^([+-])([obki])$/.exec(rest[0] || "");
        if (!m) {
          const current =
            [
              active.kind === "private" ? "+i" : null,
              active.has_password ? "+k" : null,
            ]
              .filter(Boolean)
              .join(" ") || "(none)";
          throw new Error(
            `Usage: /mode [+|-][o|b|k|i] [arg]. Current: ${current}`
          );
        }
        const on = m[1] === "+";
        switch (m[2]) {
          case "o": {
            const u = await resolveUser(rest[1]);
            await post("/role", { user_id: u.id, role: on ? "mod" : "member" });
            return {
              ok: true,
              message: on
                ? `${u.display_name} is now an operator.`
                : `${u.display_name} is no longer an operator.`,
            };
          }
          case "b": {
            const u = await resolveUser(rest[1]);
            if (on) {
              await post("/ban", {
                user_id: u.id,
                reason: rest.slice(2).join(" "),
              });
              return { ok: true, message: `Banned ${u.display_name}.` };
            }
            await post("/unban", { user_id: u.id });
            return { ok: true, message: `Unbanned ${u.display_name}.` };
          }
          case "k": {
            if (on && !rest[1]) throw new Error("Usage: /mode +k <password>");
            await updateChannel(active.id, { password: on ? rest[1] : "" });
            return {
              ok: true,
              message: on
                ? "Channel password set."
                : "Channel password removed.",
            };
          }
          default: {
            // "i": invite-only (private) vs public.
            await updateChannel(active.id, { is_private: on });
            return {
              ok: true,
              message: on
                ? "Channel is now invite-only (private)."
                : "Channel is now public.",
            };
          }
        }
      }
      default:
        throw new Error(`Unknown command: /${cmd}. Try /help`);
    }
  }

  async function joinAndOpen(channel, password) {
    const opts = { method: "POST" };
    if (password) opts.body = { password };
    await api(`/channels/${channel.id}/join`, opts);
    await refreshLists();
    openChannel(channel.id);
  }

  async function moderate(action, channelId, member) {
    const verb = action === "ban" ? "Ban" : "Kick";
    const extra =
      action === "ban" ? " They won't be able to rejoin." : "";
    const ok = await ask({
      title: `${verb} ${member.display_name}?`,
      body: extra.trim() || undefined,
      confirmLabel: verb,
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/channels/${channelId}/${action}`, {
        method: "POST",
        body: { user_id: member.id },
      });
      const rows = await api(`/channels/${channelId}/members`);
      setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    } catch (e) {
      notify(e.message, `Couldn't ${action} that member`);
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
  // Read-only (announcement) channels: only site admins may post.
  const canPost = !!active && (!active.read_only || user.is_admin);

  const isDmChannel = !!active && active.kind === "dm";
  const isGroup = !!active && active.kind === "group";
  const dmKey = isDmChannel ? sharedKeys[active.id] : null;

  // Derive the shared secret for the open DM from the peer's public key. Fails
  // quietly when the peer hasn't enabled encryption — those DMs stay plaintext.
  useEffect(() => {
    if (!isDmChannel || !privateKey || sharedKeys[active.id]) return;
    const peer = activeMembers.find((m) => m.id !== user.id);
    if (!peer) return;
    let cancelled = false;
    (async () => {
      try {
        const { public_key } = await api(`/keys/${peer.id}`);
        const shared = await deriveSharedKey(
          privateKey,
          await importPublicKey(public_key)
        );
        const mine = await api("/keys/me");
        const fp = await safetyNumber(mine.public_key, public_key);
        if (!cancelled) {
          setSharedKeys((prev) => ({ ...prev, [active.id]: shared }));
          setFingerprints((prev) => ({ ...prev, [active.id]: fp }));
        }
      } catch {
        /* peer has no key yet */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isDmChannel, active, privateKey, activeMembers, user.id, sharedKeys, keyEpoch]);

  // Group keys, per channel: { current: epoch|null, byEpoch: {epoch: AES key} }.
  // A group holds several epochs at once because the key rotates on membership
  // changes and older history is still readable with the key of its own time.
  const groupRing = isGroup ? groupKeys[active.id] : null;
  const groupKey = groupRing?.current ? groupRing.byEpoch[groupRing.current] : null;

  // Fetch and open every epoch sealed to us. Each share was wrapped under the
  // pairwise secret with whoever published it, so opening it is the same ECDH
  // we already do for 1:1 DMs.
  useEffect(() => {
    if (!isGroup || !privateKey || !active || groupKeys[active.id]) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await api(`/dms/${active.id}/keys`);
        const byEpoch = {};
        for (const k of res.keys) {
          try {
            const pairwise = await deriveSharedKey(
              privateKey,
              await importPublicKey(k.sender_public_key)
            );
            byEpoch[k.epoch] = await unwrapGroupKey(pairwise, k.wrapped_key);
          } catch {
            /* not sealed to this key; that epoch stays unreadable */
          }
        }
        if (!cancelled) {
          setGroupKeys((prev) => ({
            ...prev,
            [active.id]: { current: res.current_epoch, byEpoch },
          }));
        }
      } catch {
        /* plaintext group, or we're not a member any more */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [isGroup, active, privateKey, groupKeys, keyEpoch]);

  // Publish a fresh epoch for a group: mint a key, seal one copy per member
  // under the pairwise secret we share with them, and hand the sealed copies
  // to the server. This is the only way a group key ever moves, and the server
  // never sees an unsealed one.
  //
  // Used both to switch encryption on and to rotate after a membership change.
  // Rotating is what makes removal real: the departing member keeps the old
  // key, but it opens nothing said afterwards.
  const rekeyGroup = useCallback(
    async (channelId) => {
      if (!privateKey) throw new Error("Unlock your encryption key first");
      const members = await api(`/channels/${channelId}/members`);
      const mine = await api("/keys/me");
      const key = await generateGroupKey();
      const shares = [];
      for (const m of members) {
        // Includes ourselves: sealing to our own public key is a valid ECDH
        // only we can reproduce, and it means a new device can catch up.
        const { public_key } = await api(`/keys/${m.id}`);
        const pairwise = await deriveSharedKey(
          privateKey,
          await importPublicKey(public_key)
        );
        shares.push({
          user_id: m.id,
          wrapped_key: await wrapGroupKey(pairwise, key),
          sender_public_key: mine.public_key,
        });
      }
      await api(`/dms/${channelId}/keys`, { method: "POST", body: { shares } });
      // Drop the cached ring so the next render refetches and opens the new
      // epoch alongside the ones we already hold.
      setGroupKeys((prev) => {
        const next = { ...prev };
        delete next[channelId];
        return next;
      });
    },
    [privateKey]
  );

  // Which key opens a given message: a DM has exactly one, a group has one per
  // epoch and the message says which it used.
  function keyForMessage(m) {
    if (isDmChannel) return dmKey;
    if (isGroup) return groupRing?.byEpoch?.[m.key_epoch] || null;
    return null;
  }

  // Decrypt ciphertext for display: message bodies plus any encrypted reply
  // previews (those carry the full payload, keyed by the parent's id).
  useEffect(() => {
    if (!active || (!dmKey && !groupRing)) return;
    const list = msgsByChannel[active.id] || [];
    const todo = [];
    for (const m of list) {
      if (m.encrypted && decrypted[m.id] === undefined) {
        todo.push([m.id, m.content, keyForMessage(m)]);
      }
      const rp = m.reply_to;
      if (rp?.encrypted && decrypted[rp.id] === undefined) {
        // A reply preview is quoted from the same conversation, so it was
        // sealed under the same epoch as the message quoting it.
        todo.push([rp.id, rp.content, keyForMessage(m)]);
      }
    }
    if (!todo.length) return;
    let cancelled = false;
    (async () => {
      const updates = {};
      for (const [id, payload, key] of todo) {
        if (!key) {
          // No key for that epoch: sent before we joined, and meant to stay
          // unreadable. Recorded as null so we don't retry it every render.
          updates[id] = null;
          continue;
        }
        try {
          updates[id] = await decryptMessage(key, payload);
        } catch {
          updates[id] = null; // not decryptable with this key
        }
      }
      if (!cancelled) setDecrypted((prev) => ({ ...prev, ...updates }));
    })();
    return () => {
      cancelled = true;
    };
  }, [active, dmKey, groupRing, msgsByChannel, decrypted]);

  // Given to MessagePane; returning null means "send as plaintext". Groups also
  // report the epoch, which the server checks is still the current one.
  async function encryptForActive(text) {
    if (isDmChannel && dmKey) {
      return { content: await encryptMessage(dmKey, text) };
    }
    if (isGroup && groupKey) {
      return {
        content: await encryptMessage(groupKey, text),
        keyEpoch: groupRing.current,
      };
    }
    return null;
  }

  async function setRole(channelId, member, role) {
    try {
      await api(`/channels/${channelId}/role`, {
        method: "POST",
        body: { user_id: member.id, role },
      });
      const rows = await api(`/channels/${channelId}/members`);
      setMembersByChannel((prev) => ({ ...prev, [channelId]: rows }));
    } catch (e) {
      notify(e.message, "Couldn't change that role");
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
          ? {
              ...c,
              name: updated.name,
              topic: updated.topic,
              kind: updated.kind,
              has_password: updated.has_password,
            }
          : c
      )
    );
  }

  async function deleteChannel(channel) {
    const ok = await ask({
      title: `Delete #${channel.name}?`,
      body: "This permanently removes the channel and all of its messages, for everyone.",
      confirmLabel: "Delete channel",
      danger: true,
    });
    if (!ok) return;
    try {
      await api(`/channels/${channel.id}`, { method: "DELETE" });
      setActiveId((prev) => (prev === channel.id ? null : prev));
      await refreshLists();
    } catch (e) {
      notify(e.message, "Couldn't delete that channel");
    }
  }

  // Swipe right on the open roster drawer (or its backdrop) closes it.
  const rosterSwipe = useSwipe({ onSwipeRight: () => setRosterOpen(false) });

  return (
    <div
      className={`shell ${activeId ? "has-active" : ""} ${
        rosterOpen ? "roster-open" : ""
      }`}
    >
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
        onCloseDm={closeDM}
        onLeaveGroup={leaveGroup}
        onCreateGroup={createGroup}
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
            onPrepend={(older) =>
              setMsgsByChannel((prev) => {
                const list = prev[active.id] || [];
                const seen = new Set(list.map((m) => m.id));
                const fresh = older.filter((m) => !seen.has(m.id));
                if (!fresh.length) return prev;
                return { ...prev, [active.id]: [...fresh, ...list] };
              })
            }
            onTyping={() => {
              // Honour the preference here too, so the signal isn't even sent
              // (the server drops it as well, but no reason to emit it).
              if (user.share_typing === false) return;
              send({ type: "typing", channel_id: active.id });
            }}
            onOpenProfile={setProfileUserId}
            canDelete={!isGroup && canDelete}
            onDeleteChannel={() => deleteChannel(active)}
            canManage={isGroup || canDelete}
            onSetTopic={(topic) => updateChannel(active.id, { topic })}
            onOpenSettings={() => setSettingsOpen(true)}
            onOpenThread={openThread}
            onCommand={runCommand}
            canPost={canPost}
            jumpTo={jumpTo}
            onJumped={() => setJumpTo(null)}
            decrypted={decrypted}
            encryptContent={dmKey || groupKey ? encryptForActive : null}
            sendKey={dmKey || groupKey}
            keyForMessage={keyForMessage}
            e2ee={
              isDmChannel || isGroup
                ? {
                    ready: isGroup ? !!groupKey : !!dmKey,
                    status: keyStatus,
                    // Safety numbers are pairwise, so a group has no single
                    // fingerprint to show; the badge stands alone there.
                    fingerprint: isGroup ? null : fingerprints[active.id],
                    // A plaintext group isn't waiting on your key, it simply
                    // has none yet, so don't nag about unlocking.
                    plaintextGroup: isGroup && !groupRing?.current,
                    onUnlock: () =>
                      setE2eeModal(keyStatus === "none" ? "setup" : "unlock"),
                  }
                : null
            }
            onBack={() => {
              setActiveId(null);
              setRosterOpen(false);
            }}
            onToggleRoster={
              active.kind !== "dm"
                ? () => setRosterOpen((o) => !o)
                : null
            }
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
            canPost={canPost}
          />
        ) : active && active.kind !== "dm" ? (
          <>
            {rosterOpen && (
              <div
                className="roster-backdrop"
                onClick={() => setRosterOpen(false)}
                {...rosterSwipe}
              />
            )}
            <MemberList
              bind={rosterSwipe}
            members={activeMembers}
            online={online}
            awayMap={awayMap}
            onOpenProfile={setProfileUserId}
            canModerate={!isGroup && canModerate}
            canManageRoles={!isGroup && canManageRoles}
            myId={user.id}
            onKick={(m) => moderate("kick", active.id, m)}
            onBan={(m) => moderate("ban", active.id, m)}
            onSetRole={(m, role) => setRole(active.id, m, role)}
            />
          </>
        ) : null}
      </div>

      {profileUserId && (
        <Profile
          userId={profileUserId}
          onClose={() => setProfileUserId(null)}
          onMessage={openDM}
        />
      )}

      {dialog && (
        <ConfirmDialog
          {...dialog}
          onResolve={(answer) => {
            dialog.resolve(answer);
            setDialog(null);
          }}
        />
      )}

      {e2eeModal && (
        <E2EESetup
          mode={e2eeModal}
          onClose={() => setE2eeModal(null)}
          onUnlocked={(key) => {
            setPrivateKey(key);
            setKeyStatus("unlocked");
            setE2eeModal(null);
          }}
        />
      )}

      {searchOpen && (
        <SearchModal
          onClose={() => setSearchOpen(false)}
          onOpen={(channelId, messageId) => {
            setSearchOpen(false);
            openChannel(channelId, messageId);
          }}
        />
      )}

      {settingsOpen && active && active.kind !== "dm" && !isGroup && (
        <ChannelSettings
          onConfirm={ask}
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
      {settingsOpen && isGroup && (
        <GroupInfo
          group={active}
          members={activeMembers}
          myId={user.id}
          isOwner={isOwner}
          onConfirm={ask}
          encrypted={!!groupKeys[active.id]?.current}
          canEncrypt={keyStatus === "unlocked"}
          onEnableEncryption={() => rekeyGroup(active.id)}
          onRename={(name) => updateChannel(active.id, { name })}
          onAddMember={(userId) => addGroupMember(active.id, userId)}
          onRemoveMember={(userId) => removeGroupMember(active.id, userId)}
          onLeave={() => {
            setSettingsOpen(false);
            leaveGroup(active);
          }}
          onOpenProfile={setProfileUserId}
          onClose={() => setSettingsOpen(false)}
        />
      )}
    </div>
  );
}
