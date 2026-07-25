from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..audit import record_audit
from ..deps import DB, CurrentUser, require_membership
from ..sanitize import sanitize_text
from ..models import (
    KIND_DM,
    KIND_GROUP,
    KIND_PRIVATE,
    KIND_PUBLIC,
    ROLE_MEMBER,
    ROLE_MOD,
    ROLE_OWNER,
    Channel,
    ChannelBan,
    ChannelMember,
    Message,
    MessageMention,
    User,
)
from ..schemas import (
    ChannelCreate,
    ChannelJoin,
    ChannelOut,
    ChannelUpdate,
    MemberOut,
    ModerateIn,
    RoleUpdate,
    UserPublic,
)
from ..security import hash_password, verify_password
from ..ws_manager import manager
from .messages import announce_action

router = APIRouter(prefix="/channels", tags=["channels"])


async def _active_channel(db, channel_id: str) -> Channel:
    """Load a channel for an action, 404ing if it's missing or archived.

    Centralizes the archived check so every mutation path treats an archived
    channel as gone, the same way get/join already do — rather than each
    endpoint remembering (or forgetting) to check.
    """
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.archived:
        raise HTTPException(status_code=404, detail="Channel not found")
    return ch


async def _require_moderator(db, channel: Channel, user: User) -> None:
    """Site admins, or the channel's owner/mod, may moderate."""
    if user.is_admin:
        return
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel.id,
                ChannelMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if member is None or member.role not in (ROLE_OWNER, ROLE_MOD):
        raise HTTPException(
            status_code=403,
            detail="You don't have permission to moderate this channel",
        )


async def _require_owner(db, channel: Channel, user: User) -> None:
    """Site admins, or the channel's owner, may perform owner-only actions.

    The owner-or-admin gate was hand-rolled in four endpoints; this is the one
    copy they now share.
    """
    if user.is_admin:
        return
    member = await _target_member(db, channel.id, user.id)
    if member is None or member.role != ROLE_OWNER:
        raise HTTPException(
            status_code=403,
            detail="Only the channel owner or a site admin can do that",
        )


async def _member_count(db, channel_id: str) -> int:
    return (
        await db.execute(
            select(func.count())
            .select_from(ChannelMember)
            .where(ChannelMember.channel_id == channel_id)
        )
    ).scalar_one()


async def _is_member(db, channel_id: str, user_id: str) -> bool:
    return (
        await db.execute(
            select(ChannelMember.id).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none() is not None


async def _channel_stats(
    db, channel_ids: list[str], user_id: str
) -> dict[str, dict]:
    """Batch the per-channel figures the list endpoints need.

    Returns ``{channel_id: {member_count, is_member, unread, mentions}}`` using
    a constant number of grouped queries, instead of the previous ~5 queries per
    channel (member count, membership, and a three-query unread/mention count
    looped over every channel).
    """
    stats = {
        cid: {"member_count": 0, "is_member": False, "unread": 0, "mentions": 0}
        for cid in channel_ids
    }
    if not channel_ids:
        return stats

    # Member counts for every channel, one grouped query.
    for cid, cnt in (
        await db.execute(
            select(ChannelMember.channel_id, func.count())
            .where(ChannelMember.channel_id.in_(channel_ids))
            .group_by(ChannelMember.channel_id)
        )
    ).all():
        stats[cid]["member_count"] = int(cnt)

    # The caller's own membership rows carry both is_member and each channel's
    # last_read_at, in one query.
    for m in (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id.in_(channel_ids),
                ChannelMember.user_id == user_id,
            )
        )
    ).scalars().all():
        stats[m.channel_id]["is_member"] = True

    # Unread and mention counts. Joining messages against the caller's own
    # membership row measures each channel from its own last_read_at, so both
    # figures come from one grouped query each (channels the caller hasn't
    # joined match no membership row and stay at zero, as before).
    my_membership = (ChannelMember.channel_id == Message.channel_id) & (
        ChannelMember.user_id == user_id
    )
    unread_where = (
        Message.channel_id.in_(channel_ids),
        Message.deleted_at.is_(None),
        Message.sender_id != user_id,
        Message.created_at > ChannelMember.last_read_at,
    )
    for cid, cnt in (
        await db.execute(
            select(Message.channel_id, func.count())
            .join(ChannelMember, my_membership)
            .where(*unread_where)
            .group_by(Message.channel_id)
        )
    ).all():
        stats[cid]["unread"] = int(cnt)

    for cid, cnt in (
        await db.execute(
            select(Message.channel_id, func.count())
            .select_from(MessageMention)
            .join(Message, Message.id == MessageMention.message_id)
            .join(ChannelMember, my_membership)
            .where(MessageMention.user_id == user_id, *unread_where)
            .group_by(Message.channel_id)
        )
    ).all():
        stats[cid]["mentions"] = int(cnt)

    return stats


def _to_out(
    ch: Channel,
    member_count: int,
    is_member: bool,
    unread: int = 0,
    mentions: int = 0,
) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        kind=ch.kind,
        slug=ch.slug,
        name=ch.name,
        topic=ch.topic,
        created_by=ch.created_by,
        created_at=ch.created_at,
        read_only=ch.read_only,
        has_password=bool(ch.password_hash),
        member_count=member_count,
        is_member=is_member,
        unread_count=unread,
        mention_count=mentions,
    )


@router.get("", response_model=list[ChannelOut])
async def list_channels(db: DB, user: CurrentUser) -> list[ChannelOut]:
    """Public channel directory + any private channels the user belongs to.

    DMs are excluded here — they live under /dms.
    """
    # Public channels (everyone can see/browse).
    public = (
        await db.execute(
            select(Channel).where(
                Channel.kind == KIND_PUBLIC, Channel.archived.is_(False)
            )
        )
    ).scalars().all()

    # Private channels the user is a member of.
    private = (
        await db.execute(
            select(Channel)
            .join(ChannelMember, ChannelMember.channel_id == Channel.id)
            .where(
                Channel.kind == KIND_PRIVATE,
                Channel.archived.is_(False),
                ChannelMember.user_id == user.id,
            )
        )
    ).scalars().all()

    all_channels = [*public, *private]
    stats = await _channel_stats(db, [ch.id for ch in all_channels], user.id)
    return [
        _to_out(
            ch,
            stats[ch.id]["member_count"],
            stats[ch.id]["is_member"],
            stats[ch.id]["unread"],
            stats[ch.id]["mentions"],
        )
        for ch in all_channels
    ]


# Non-admin users may create at most this many channels (DMs don't count).
MAX_CHANNELS_PER_USER = 2


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, db: DB, user: CurrentUser) -> ChannelOut:
    if not user.is_admin:
        created = (
            await db.execute(
                select(func.count())
                .select_from(Channel)
                .where(Channel.created_by == user.id, Channel.kind != KIND_DM)
            )
        ).scalar_one()
        if created >= MAX_CHANNELS_PER_USER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"You can create at most {MAX_CHANNELS_PER_USER} channels. "
                    "Delete one first, or ask an admin."
                ),
            )

    existing = (
        await db.execute(select(Channel).where(Channel.slug == body.slug))
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Channel slug already exists"
        )
    ch = Channel(
        kind=KIND_PRIVATE if body.is_private else KIND_PUBLIC,
        slug=body.slug,
        name=body.name,
        topic=body.topic,
        created_by=user.id,
        # A channel key only applies to public channels; a private channel is
        # already invite-gated, so any password sent with one is ignored.
        password_hash=(
            hash_password(body.password)
            if body.password and not body.is_private
            else None
        ),
    )
    db.add(ch)
    await db.flush()  # get ch.id
    db.add(ChannelMember(channel_id=ch.id, user_id=user.id, role=ROLE_OWNER))
    await db.commit()
    return _to_out(ch, 1, True)


@router.get("/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: str, db: DB, user: CurrentUser) -> ChannelOut:
    ch = await _active_channel(db, channel_id)
    is_member = await _is_member(db, ch.id, user.id)
    if ch.kind in (KIND_PRIVATE, KIND_DM, KIND_GROUP) and not is_member:
        raise HTTPException(
            status_code=403, detail="You are not a member of this channel"
        )
    return _to_out(ch, await _member_count(db, ch.id), is_member)


@router.post("/{channel_id}/join", response_model=ChannelOut)
async def join_channel(
    channel_id: str, db: DB, user: CurrentUser, body: ChannelJoin = ChannelJoin()
) -> ChannelOut:
    ch = await _active_channel(db, channel_id)
    if ch.kind != KIND_PUBLIC:
        raise HTTPException(
            status_code=403, detail="This channel cannot be joined directly"
        )
    banned = (
        await db.execute(
            select(ChannelBan).where(
                ChannelBan.channel_id == ch.id, ChannelBan.user_id == user.id
            )
        )
    ).scalar_one_or_none()
    if banned is not None:
        raise HTTPException(
            status_code=403, detail="You are banned from this channel"
        )
    already_member = await _is_member(db, ch.id, user.id)
    # Channel key check. Existing members re-affirming, and site admins, skip it;
    # the owner is always already a member (auto-joined at creation).
    if ch.password_hash and not already_member and not user.is_admin:
        if not body.password:
            raise HTTPException(
                status_code=403, detail="This channel requires a password"
            )
        if not verify_password(body.password, ch.password_hash):
            raise HTTPException(
                status_code=403, detail="Incorrect channel password"
            )
    if not already_member:
        db.add(ChannelMember(channel_id=ch.id, user_id=user.id, role=ROLE_MEMBER))
        # IRC-style join notice; announce_action commits the membership with it.
        await announce_action(db, ch.id, user, "joined the channel")
    return _to_out(ch, await _member_count(db, ch.id), True)


@router.post("/{channel_id}/leave", status_code=status.HTTP_204_NO_CONTENT)
async def leave_channel(channel_id: str, db: DB, user: CurrentUser) -> None:
    member = await require_membership(db, channel_id, user.id)
    ch = await db.get(Channel, channel_id)
    if ch is not None and ch.read_only:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can't leave the announcements channel.",
        )
    if ch is not None and ch.kind == KIND_DM:
        # A DM is left via close (which only hides it); hard-deleting the
        # membership here would corrupt the peer's view of the conversation.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Close a direct message instead of leaving it.",
        )
    await db.delete(member)
    # Part notice for public channels and group DMs (private-channel membership
    # is managed via invite/kick, which announce separately). announce_action
    # commits the membership removal together with the notice.
    if ch is not None and ch.kind in (KIND_PUBLIC, KIND_GROUP):
        where = "the group" if ch.kind == KIND_GROUP else "the channel"
        await announce_action(db, channel_id, user, f"left {where}")
    else:
        await db.commit()


@router.get("/{channel_id}/members", response_model=list[MemberOut])
async def channel_members(
    channel_id: str, db: DB, user: CurrentUser
) -> list[MemberOut]:
    await require_membership(db, channel_id, user.id)
    rows = (
        await db.execute(
            select(User, ChannelMember.role)
            .join(ChannelMember, ChannelMember.user_id == User.id)
            .where(ChannelMember.channel_id == channel_id)
        )
    ).all()
    return [
        MemberOut(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            avatar_url=u.avatar_url,
            is_admin=u.is_admin,
            role=role,
        )
        for u, role in rows
    ]


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: str, body: ChannelUpdate, db: DB, user: CurrentUser
) -> ChannelOut:
    ch = await _active_channel(db, channel_id)
    await _require_owner(db, ch, user)
    # Captured before any change so the redacted key notice can say set vs
    # changed vs removed. The key value itself is never announced.
    had_password = bool(ch.password_hash)
    password_notice: str | None = None
    if body.name is not None:
        ch.name = sanitize_text(body.name, max_length=64) or ch.name
    if body.topic is not None:
        ch.topic = sanitize_text(body.topic, max_length=512)
    if body.is_private is not None and ch.kind != KIND_DM:
        ch.kind = KIND_PRIVATE if body.is_private else KIND_PUBLIC
    # A private channel is invite-gated and never carries a channel key.
    if ch.kind == KIND_PRIVATE:
        ch.password_hash = None
    # "password" absent = leave as-is; "" or null = remove; non-empty = set.
    if "password" in body.model_fields_set:
        pw = body.password
        if pw:
            if ch.kind != KIND_PUBLIC:
                raise HTTPException(
                    status_code=400,
                    detail="Password protection only applies to public channels",
                )
            if len(pw) < 8:
                raise HTTPException(
                    status_code=400,
                    detail="Channel password must be at least 8 characters",
                )
            ch.password_hash = hash_password(pw)
            password_notice = (
                "changed the channel password"
                if had_password
                else "set a channel password"
            )
        else:
            if had_password:
                password_notice = "removed the channel password"
            ch.password_hash = None
    await db.commit()
    # Push the change to everyone viewing the channel.
    await manager.publish_room(
        channel_id,
        {
            "type": "channel_updated",
            "data": {
                "channel_id": channel_id,
                "name": ch.name,
                "topic": ch.topic,
                "kind": ch.kind,
                "has_password": bool(ch.password_hash),
            },
        },
    )
    # Redacted key notice: members learn a password was set/changed/removed,
    # never what it is. Posted after the commit as its own system message.
    if password_notice:
        await announce_action(db, channel_id, user, password_notice)
    return _to_out(ch, await _member_count(db, ch.id), True)


async def _target_member(db, channel_id: str, target_id: str) -> ChannelMember | None:
    return (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == target_id,
            )
        )
    ).scalar_one_or_none()


async def _announce_removal(channel_id: str, target_id: str, banned: bool) -> None:
    # Tell the room to refresh its roster, and tell the removed user to leave.
    await manager.publish_room(
        channel_id,
        {"type": "member_removed", "data": {"channel_id": channel_id, "user_id": target_id}},
    )
    await manager.publish_user(
        target_id,
        {"type": "channel_kicked", "data": {"channel_id": channel_id, "banned": banned}},
    )


@router.post("/{channel_id}/kick", status_code=status.HTTP_204_NO_CONTENT)
async def kick_member(
    channel_id: str, body: ModerateIn, db: DB, user: CurrentUser
) -> None:
    ch = await _active_channel(db, channel_id)
    await _require_moderator(db, ch, user)
    if body.user_id == user.id:
        raise HTTPException(status_code=400, detail="Use leave to remove yourself")
    member = await _target_member(db, channel_id, body.user_id)
    if member is None:
        raise HTTPException(status_code=404, detail="User is not a member")
    if member.role == ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Cannot remove the channel owner")
    await db.delete(member)
    record_audit(
        db, user.id, "channel.kick",
        target_user_id=body.user_id, channel_id=channel_id,
    )
    await db.commit()
    await _announce_removal(channel_id, body.user_id, banned=False)


@router.post("/{channel_id}/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_member(
    channel_id: str, body: ModerateIn, db: DB, user: CurrentUser
) -> None:
    ch = await _active_channel(db, channel_id)
    await _require_moderator(db, ch, user)
    if body.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot ban yourself")
    target = await db.get(User, body.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")

    member = await _target_member(db, channel_id, body.user_id)
    if member is not None and member.role == ROLE_OWNER:
        raise HTTPException(status_code=403, detail="Cannot ban the channel owner")
    if member is not None:
        await db.delete(member)

    existing = (
        await db.execute(
            select(ChannelBan).where(
                ChannelBan.channel_id == channel_id,
                ChannelBan.user_id == body.user_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        db.add(
            ChannelBan(
                channel_id=channel_id,
                user_id=body.user_id,
                banned_by=user.id,
                reason=body.reason,
            )
        )
    record_audit(
        db, user.id, "channel.ban",
        target_user_id=body.user_id, channel_id=channel_id, detail=body.reason,
    )
    await db.commit()
    await _announce_removal(channel_id, body.user_id, banned=True)


@router.post("/{channel_id}/unban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_member(
    channel_id: str, body: ModerateIn, db: DB, user: CurrentUser
) -> None:
    ch = await _active_channel(db, channel_id)
    await _require_moderator(db, ch, user)
    ban = (
        await db.execute(
            select(ChannelBan).where(
                ChannelBan.channel_id == channel_id,
                ChannelBan.user_id == body.user_id,
            )
        )
    ).scalar_one_or_none()
    if ban is not None:
        await db.delete(ban)
        record_audit(
            db, user.id, "channel.unban",
            target_user_id=body.user_id, channel_id=channel_id,
        )
        await db.commit()


@router.post("/{channel_id}/invite", status_code=status.HTTP_204_NO_CONTENT)
async def invite_member(
    channel_id: str, body: ModerateIn, db: DB, user: CurrentUser
) -> None:
    """Add a user to a channel (any member may invite; key for private channels)."""
    ch = await _active_channel(db, channel_id)
    if ch.kind == KIND_DM:
        raise HTTPException(status_code=400, detail="Not applicable to direct messages")
    await require_membership(db, channel_id, user.id)
    # A password-protected channel gates entry on the key; only a moderator may
    # add someone directly, so an ordinary member can't invite around the key.
    if ch.password_hash:
        await _require_moderator(db, ch, user)

    target = await db.get(User, body.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    banned = (
        await db.execute(
            select(ChannelBan).where(
                ChannelBan.channel_id == channel_id,
                ChannelBan.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if banned is not None:
        raise HTTPException(
            status_code=403, detail="That user is banned from this channel"
        )

    existing = await _target_member(db, channel_id, target.id)
    if existing is None:
        db.add(
            ChannelMember(channel_id=channel_id, user_id=target.id, role=ROLE_MEMBER)
        )
        await announce_action(
            db, channel_id, user, f"added @{target.username} to the channel"
        )
        await manager.publish_user(
            target.id, {"type": "channel_added", "data": {"channel_id": channel_id}}
        )


@router.post("/{channel_id}/role", status_code=status.HTTP_204_NO_CONTENT)
async def set_member_role(
    channel_id: str, body: RoleUpdate, db: DB, user: CurrentUser
) -> None:
    """Grant or revoke channel operator (mod) status. Owner or site admin only."""
    ch = await _active_channel(db, channel_id)
    if ch.kind == KIND_DM:
        raise HTTPException(status_code=400, detail="Not applicable to direct messages")
    if body.role not in (ROLE_OWNER, ROLE_MOD, ROLE_MEMBER):
        raise HTTPException(
            status_code=400, detail="Role must be 'owner', 'mod', or 'member'"
        )

    # Only the channel owner or a site admin can assign roles.
    await _require_owner(db, ch, user)
    if body.user_id == user.id:
        raise HTTPException(status_code=400, detail="You cannot change your own role")

    target = await _target_member(db, channel_id, body.user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User is not a member")

    if body.role == ROLE_OWNER:
        # Ownership transfer: demote the current owner(s) to mod, promote target.
        current_owners = (
            await db.execute(
                select(ChannelMember).where(
                    ChannelMember.channel_id == channel_id,
                    ChannelMember.role == ROLE_OWNER,
                )
            )
        ).scalars().all()
        for owner in current_owners:
            owner.role = ROLE_MOD
        target.role = ROLE_OWNER
        action = "channel.transfer_owner"
    else:
        if target.role == ROLE_OWNER:
            raise HTTPException(
                status_code=403, detail="Demote via ownership transfer instead"
            )
        target.role = body.role
        action = "channel.op" if body.role == ROLE_MOD else "channel.deop"

    record_audit(
        db, user.id, action, target_user_id=body.user_id, channel_id=channel_id
    )

    # Announce the change in the channel as an action from the actor.
    target_user = await db.get(User, body.user_id)
    handle = target_user.username if target_user else "user"
    if body.role == ROLE_OWNER:
        text = f"transferred channel ownership to @{handle}"
    elif body.role == ROLE_MOD:
        text = f"gave @{handle} operator status (+o)"
    else:
        text = f"removed operator status (-o) from @{handle}"
    # Commits the role change + audit together with the announcement message.
    await announce_action(db, channel_id, user, text)

    await manager.publish_room(
        channel_id,
        {
            "type": "member_updated",
            "data": {"channel_id": channel_id, "user_id": body.user_id, "role": body.role},
        },
    )


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: str, db: DB, user: CurrentUser) -> None:
    """Delete a channel and all its messages. Site admins or the channel owner
    only. DMs are not deletable here."""
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.kind == KIND_DM:
        raise HTTPException(
            status_code=400, detail="Direct messages cannot be deleted"
        )
    if ch.read_only:
        raise HTTPException(
            status_code=403, detail="The announcements channel can't be deleted."
        )
    await _require_owner(db, ch, user)

    # Record before deletion (the channel_id FK gets nulled by the cascade, so
    # keep the name in `detail`).
    record_audit(db, user.id, "channel.delete", detail=f"#{ch.slug or ch.name}")
    # DB-level ON DELETE CASCADE removes members, messages, reactions, mentions,
    # and channel bans.
    await db.delete(ch)
    await db.commit()

    # Notify connected members so their UI drops the channel.
    await manager.publish_room(
        channel_id, {"type": "channel_deleted", "data": {"channel_id": channel_id}}
    )


@router.post("/{channel_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(channel_id: str, db: DB, user: CurrentUser) -> None:
    """Mark everything up to now as read, clearing this channel's badge."""
    member = await require_membership(db, channel_id, user.id)
    member.last_read_at = datetime.now(timezone.utc)
    await db.commit()
