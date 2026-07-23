from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..config import settings
from ..deps import DB, CurrentUser, require_membership
from ..models import (
    KIND_DM,
    Channel,
    ChannelMember,
    Message,
    MessageMention,
    MessageReaction,
    Upload,
    User,
)
from ..redis_client import redis_client
from ..sanitize import extract_mention_usernames, sanitize_text
from ..schemas import (
    AttachmentOut,
    MentionOut,
    MessageCreate,
    MessageEdit,
    MessageOut,
    ReactionIn,
    ReactionSummary,
    ReplyPreview,
    UserPublic,
)
from ..ws_manager import manager
from .uploads import attachment_out

router = APIRouter(prefix="/channels/{channel_id}/messages", tags=["messages"])


async def rate_limited(user_id: str) -> bool:
    """Sliding-ish window: N messages per 10s per user, tracked in Redis."""
    key = f"rl:msg:{user_id}"
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 10)
    return count > settings.msg_rate_per_10s


async def _reactions_for(
    db, message_ids: list[str], me_id: str
) -> dict[str, list[ReactionSummary]]:
    """Aggregate reactions for a batch of messages into per-message summaries."""
    if not message_ids:
        return {}
    rows = (
        await db.execute(
            select(MessageReaction).where(
                MessageReaction.message_id.in_(message_ids)
            )
        )
    ).scalars().all()
    # message_id -> emoji -> {count, me}
    agg: dict[str, dict[str, dict]] = {}
    for r in rows:
        emojis = agg.setdefault(r.message_id, {})
        entry = emojis.setdefault(r.emoji, {"count": 0, "me": False})
        entry["count"] += 1
        if r.user_id == me_id:
            entry["me"] = True
    return {
        mid: [
            ReactionSummary(emoji=e, count=v["count"], me=v["me"])
            for e, v in emojis.items()
        ]
        for mid, emojis in agg.items()
    }


async def announce_action(db, channel_id: str, actor: User, text: str) -> None:
    """Post a /me-style action message from `actor` into a channel and
    broadcast it (commits the session, so pending changes land with it)."""
    msg = Message(channel_id=channel_id, sender_id=actor.id, content=f"/me {text}")
    db.add(msg)
    await db.flush()
    mentioned = await _resolve_mentions(db, msg.content)
    await _replace_mentions(db, msg.id, mentioned)
    await db.commit()
    out = _msg_out_from_user(msg, actor, mentions=_mention_outs(mentioned))
    await manager.publish_room(
        channel_id, {"type": "message", "data": out.model_dump(mode="json")}
    )


async def _resolve_mentions(db, content: str) -> list[User]:
    """Map @usernames in content to real, active users (case-insensitive)."""
    names = extract_mention_usernames(content)
    if not names:
        return []
    rows = (
        await db.execute(
            select(User).where(
                func.lower(User.username).in_(names), User.is_active.is_(True)
            )
        )
    ).scalars().all()
    return list(rows)


async def _replace_mentions(db, message_id: str, users: list[User]) -> None:
    """Persist the mention set for a message, replacing any previous set."""
    await db.execute(
        MessageMention.__table__.delete().where(
            MessageMention.message_id == message_id
        )
    )
    for u in users:
        db.add(MessageMention(message_id=message_id, user_id=u.id))


def _mention_outs(users: list[User]) -> list[MentionOut]:
    return [
        MentionOut(id=u.id, username=u.username, display_name=u.display_name)
        for u in users
    ]


def _reply_preview(parent: Message) -> ReplyPreview:
    if parent.deleted_at:
        content, encrypted = "(deleted message)", False
    elif parent.encrypted:
        # Pass the full ciphertext through — slicing it would make it
        # undecryptable. The client truncates after decrypting.
        content, encrypted = parent.content, True
    else:
        content, encrypted = parent.content[:140], False
    return ReplyPreview(
        id=parent.id,
        sender_name=(parent.sender.display_name if parent.sender else "Unknown"),
        content=content,
        encrypted=encrypted,
    )


def _msg_out(
    m: Message,
    reactions: list[ReactionSummary] | None = None,
    reply_to: ReplyPreview | None = None,
    mentions: list[MentionOut] | None = None,
    reply_count: int = 0,
    last_reply_at=None,
    attachment: AttachmentOut | None = None,
) -> MessageOut:
    return MessageOut(
        id=m.id,
        channel_id=m.channel_id,
        sender_id=m.sender_id,
        content=m.content,
        created_at=m.created_at,
        edited_at=m.edited_at,
        sender=UserPublic.model_validate(m.sender) if m.sender else None,
        reactions=reactions or [],
        reply_to=reply_to,
        mentions=mentions or [],
        thread_root_id=m.thread_root_id,
        reply_count=reply_count,
        last_reply_at=last_reply_at,
        encrypted=m.encrypted,
        attachment=attachment,
    )


async def _enrich(db, rows: list[Message], user_id: str) -> list[MessageOut]:
    """Attach reactions, reply previews, mentions, and thread counts to a batch
    of messages (sender relationship must already be loaded)."""
    if not rows:
        return []
    ids = [m.id for m in rows]
    reactions = await _reactions_for(db, ids, user_id)

    # Reply parents for inline previews.
    parent_ids = {m.reply_to_id for m in rows if m.reply_to_id}
    parents: dict[str, ReplyPreview] = {}
    if parent_ids:
        prows = (
            await db.execute(
                select(Message)
                .options(selectinload(Message.sender))
                .where(Message.id.in_(parent_ids))
            )
        ).scalars().all()
        parents = {p.id: _reply_preview(p) for p in prows}

    # Mentions.
    mentions: dict[str, list[MentionOut]] = {}
    mrows = (
        await db.execute(
            select(MessageMention.message_id, User)
            .join(User, User.id == MessageMention.user_id)
            .where(MessageMention.message_id.in_(ids))
        )
    ).all()
    for message_id, u in mrows:
        mentions.setdefault(message_id, []).append(
            MentionOut(id=u.id, username=u.username, display_name=u.display_name)
        )

    # Thread reply counts (for root messages).
    counts: dict[str, tuple[int, object]] = {}
    trows = (
        await db.execute(
            select(
                Message.thread_root_id,
                func.count(),
                func.max(Message.created_at),
            )
            .where(
                Message.thread_root_id.in_(ids), Message.deleted_at.is_(None)
            )
            .group_by(Message.thread_root_id)
        )
    ).all()
    for root_id, cnt, last_at in trows:
        counts[root_id] = (cnt, last_at)

    # Attachments.
    upload_ids = {m.upload_id for m in rows if m.upload_id}
    uploads: dict[str, AttachmentOut] = {}
    if upload_ids:
        urows = (
            await db.execute(select(Upload).where(Upload.id.in_(upload_ids)))
        ).scalars().all()
        uploads = {u.id: attachment_out(u) for u in urows}

    out = []
    for m in rows:
        cnt, last_at = counts.get(m.id, (0, None))
        out.append(
            _msg_out(
                m,
                reactions.get(m.id, []),
                parents.get(m.reply_to_id) if m.reply_to_id else None,
                mentions.get(m.id, []),
                reply_count=cnt,
                last_reply_at=last_at,
                attachment=uploads.get(m.upload_id) if m.upload_id else None,
            )
        )
    return out


@router.get("", response_model=list[MessageOut])
async def history(
    channel_id: str,
    db: DB,
    user: CurrentUser,
    before: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[MessageOut]:
    await require_membership(db, channel_id, user.id)
    stmt = (
        select(Message)
        .options(selectinload(Message.sender))
        .where(
            Message.channel_id == channel_id,
            Message.deleted_at.is_(None),
            Message.thread_root_id.is_(None),  # top-level only; replies live in threads
        )
    )
    if before is not None:
        stmt = stmt.where(Message.created_at < before)
    stmt = stmt.order_by(Message.created_at.desc()).limit(limit)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.reverse()  # chronological (oldest first) for rendering
    return await _enrich(db, rows, user.id)


@router.get("/{root_id}/thread", response_model=list[MessageOut])
async def thread(
    channel_id: str, root_id: str, db: DB, user: CurrentUser
) -> list[MessageOut]:
    """Return a thread's root message followed by its replies (chronological)."""
    await require_membership(db, channel_id, user.id)
    root = (
        await db.execute(
            select(Message)
            .options(selectinload(Message.sender))
            .where(Message.id == root_id, Message.channel_id == channel_id)
        )
    ).scalar_one_or_none()
    if root is None or root.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Thread not found")
    replies = (
        await db.execute(
            select(Message)
            .options(selectinload(Message.sender))
            .where(
                Message.thread_root_id == root_id, Message.deleted_at.is_(None)
            )
            .order_by(Message.created_at.asc())
        )
    ).scalars().all()
    return await _enrich(db, [root, *replies], user.id)


@router.post("", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def post_message(
    channel_id: str, body: MessageCreate, db: DB, user: CurrentUser
) -> MessageOut:
    await require_membership(db, channel_id, user.id)
    # Read-only (announcement) channels: only site admins may post.
    channel = await db.get(Channel, channel_id)
    if channel is not None and channel.read_only and not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This channel is read-only — you can react but not post.",
        )
    if await rate_limited(user.id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Slow down — you're sending messages too fast",
        )

    reply_preview: ReplyPreview | None = None
    if body.reply_to_id:
        parent = (
            await db.execute(
                select(Message)
                .options(selectinload(Message.sender))
                .where(Message.id == body.reply_to_id)
            )
        ).scalar_one_or_none()
        if (
            parent is None
            or parent.channel_id != channel_id
            or parent.deleted_at is not None
        ):
            raise HTTPException(
                status_code=400, detail="Reply target not found in this channel"
            )
        reply_preview = _reply_preview(parent)

    # Encrypted messages are opaque base64: the server can't sanitize, scan for
    # mentions, or index them, and must store the payload byte-for-byte or it
    # won't decrypt. Restricted to DMs — that's the only surface phase one
    # covers, and it keeps public channels searchable and moderatable.
    if body.encrypted:
        if channel is None or channel.kind != KIND_DM:
            raise HTTPException(
                status_code=400,
                detail="Encrypted messages are only supported in direct messages",
            )
        content = body.content.strip()
    else:
        content = sanitize_text(body.content, max_length=4000, allow_newlines=True)

    # Validate the attachment (if any); content may be empty when a file is sent.
    attachment = None
    if body.upload_id:
        up = await db.get(Upload, body.upload_id)
        if up is None:
            raise HTTPException(status_code=400, detail="Attachment not found")
        attachment = attachment_out(up)
    if not content and attachment is None:
        raise HTTPException(status_code=422, detail="Message cannot be empty")

    # Resolve the thread root (flatten: replying to a reply joins its thread).
    thread_root_id = None
    if body.thread_root_id:
        root = await db.get(Message, body.thread_root_id)
        if root is None or root.channel_id != channel_id or root.deleted_at:
            raise HTTPException(
                status_code=400, detail="Thread root not found in this channel"
            )
        thread_root_id = root.thread_root_id or root.id

    msg = Message(
        channel_id=channel_id,
        sender_id=user.id,
        content=content,
        reply_to_id=body.reply_to_id,
        thread_root_id=thread_root_id,
        upload_id=body.upload_id,
        encrypted=body.encrypted,
    )
    db.add(msg)
    await db.flush()  # assign msg.id before storing mentions

    # A new DM message un-closes the conversation for anyone who hid it, so
    # "close" behaves as dismissal rather than a permanent block.
    if channel is not None and channel.kind == KIND_DM:
        await db.execute(
            ChannelMember.__table__.update()
            .where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.hidden.is_(True),
            )
            .values(hidden=False)
        )

    # Mentions can't be parsed out of ciphertext; encrypted DMs have none.
    mentioned = [] if body.encrypted else await _resolve_mentions(db, content)
    await _replace_mentions(db, msg.id, mentioned)
    await db.commit()

    out = _msg_out_from_user(
        msg, user, reply_preview, mentions=_mention_outs(mentioned),
        attachment=attachment,
    )
    # Fan out to every connected member across all workers.
    await manager.publish_room(
        channel_id, {"type": "message", "data": out.model_dump(mode="json")}
    )
    return out


@router.patch("/{message_id}", response_model=MessageOut)
async def edit_message(
    channel_id: str,
    message_id: str,
    body: MessageEdit,
    db: DB,
    user: CurrentUser,
) -> MessageOut:
    await require_membership(db, channel_id, user.id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.channel_id != channel_id or msg.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.sender_id != user.id:
        raise HTTPException(
            status_code=403, detail="You can only edit your own messages"
        )
    if msg.encrypted:
        # Would require re-encrypting client-side; not supported in phase one
        # (the UI hides Edit on encrypted messages).
        raise HTTPException(
            status_code=400, detail="Encrypted messages can't be edited"
        )
    content = sanitize_text(body.content, max_length=4000, allow_newlines=True)
    if not content:
        raise HTTPException(status_code=422, detail="Message cannot be empty")
    msg.content = content
    msg.edited_at = datetime.now(timezone.utc)

    mentioned = await _resolve_mentions(db, content)
    await _replace_mentions(db, msg.id, mentioned)
    await db.commit()

    mention_outs = _mention_outs(mentioned)
    await manager.publish_room(
        channel_id,
        {
            "type": "message_edited",
            "data": {
                "id": msg.id,
                "channel_id": channel_id,
                "content": msg.content,
                "edited_at": msg.edited_at.isoformat(),
                "mentions": [m.model_dump(mode="json") for m in mention_outs],
            },
        },
    )
    return _msg_out_from_user(msg, user, mentions=mention_outs)


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_message(
    channel_id: str, message_id: str, db: DB, user: CurrentUser
) -> None:
    msg = await db.get(Message, message_id)
    if msg is None or msg.channel_id != channel_id:
        raise HTTPException(status_code=404, detail="Message not found")
    if msg.sender_id != user.id and not user.is_admin:
        raise HTTPException(status_code=403, detail="Not your message")
    msg.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    await manager.publish_room(
        channel_id, {"type": "message_deleted", "data": {"id": message_id}}
    )


@router.post("/{message_id}/reactions", response_model=ReactionSummary)
async def toggle_reaction(
    channel_id: str,
    message_id: str,
    body: ReactionIn,
    db: DB,
    user: CurrentUser,
) -> ReactionSummary:
    await require_membership(db, channel_id, user.id)
    msg = await db.get(Message, message_id)
    if msg is None or msg.channel_id != channel_id or msg.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Message not found")

    emoji = body.emoji
    existing = (
        await db.execute(
            select(MessageReaction).where(
                MessageReaction.message_id == message_id,
                MessageReaction.user_id == user.id,
                MessageReaction.emoji == emoji,
            )
        )
    ).scalar_one_or_none()

    if existing is not None:
        await db.delete(existing)
        added = False
    else:
        db.add(
            MessageReaction(message_id=message_id, user_id=user.id, emoji=emoji)
        )
        added = True
    await db.commit()

    count = (
        await db.execute(
            select(func.count())
            .select_from(MessageReaction)
            .where(
                MessageReaction.message_id == message_id,
                MessageReaction.emoji == emoji,
            )
        )
    ).scalar_one()

    # Broadcast the delta; clients reconcile counts and their own "me" flag
    # from user_id. (Can't broadcast a per-recipient "me".)
    await manager.publish_room(
        channel_id,
        {
            "type": "reaction",
            "data": {
                "message_id": message_id,
                "channel_id": channel_id,
                "emoji": emoji,
                "count": count,
                "user_id": user.id,
                "added": added,
            },
        },
    )
    return ReactionSummary(emoji=emoji, count=count, me=added)


def _msg_out_from_user(
    msg: Message,
    user: User,
    reply_to: ReplyPreview | None = None,
    mentions: list[MentionOut] | None = None,
    attachment: AttachmentOut | None = None,
) -> MessageOut:
    """Build a MessageOut when the sender is known to be `user` (no reactions
    yet, or reactions unchanged by this op)."""
    return MessageOut(
        id=msg.id,
        channel_id=msg.channel_id,
        sender_id=msg.sender_id,
        content=msg.content,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
        sender=UserPublic(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            avatar_url=user.avatar_url,
            is_admin=user.is_admin,
        ),
        reactions=[],
        reply_to=reply_to,
        mentions=mentions or [],
        thread_root_id=msg.thread_root_id,
        encrypted=msg.encrypted,
        attachment=attachment,
    )
