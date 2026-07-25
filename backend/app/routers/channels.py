from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select

from ..audit import record_audit
from ..deps import DB, CurrentUser, require_membership
from ..sanitize import sanitize_text
from ..models import (
    KIND_DM,
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


async def _unread_for(db, channel_id: str, user_id: str) -> tuple[int, int]:
    """(unread, mentions) since the member's last_read_at. Own messages and
    deleted ones don't count."""
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        return 0, 0
    base = (
        select(Message.id)
        .where(
            Message.channel_id == channel_id,
            Message.created_at > member.last_read_at,
            Message.deleted_at.is_(None),
            Message.sender_id != user_id,
        )
        .subquery()
    )
    unread = (
        await db.execute(select(func.count()).select_from(base))
    ).scalar_one()
    mentions = (
        await db.execute(
            select(func.count())
            .select_from(MessageMention)
            .where(
                MessageMention.user_id == user_id,
                MessageMention.message_id.in_(select(base.c.id)),
            )
        )
    ).scalar_one()
    return int(unread), int(mentions)


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

    out: list[ChannelOut] = []
    for ch in [*public, *private]:
        unread, mentions = await _unread_for(db, ch.id, user.id)
        out.append(
            _to_out(
                ch,
                await _member_count(db, ch.id),
                await _is_member(db, ch.id, user.id),
                unread,
                mentions,
            )
        )
    return out


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
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.archived:
        raise HTTPException(status_code=404, detail="Channel not found")
    is_member = await _is_member(db, ch.id, user.id)
    if ch.kind in (KIND_PRIVATE, KIND_DM) and not is_member:
        raise HTTPException(status_code=403, detail="Not a member")
    return _to_out(ch, await _member_count(db, ch.id), is_member)


@router.post("/{channel_id}/join", response_model=ChannelOut)
async def join_channel(
    channel_id: str, db: DB, user: CurrentUser, body: ChannelJoin = ChannelJoin()
) -> ChannelOut:
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.archived:
        raise HTTPException(status_code=404, detail="Channel not found")
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
        await db.commit()
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
    await db.delete(member)
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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    member = await require_membership(db, channel_id, user.id)
    if member.role not in (ROLE_OWNER,) and not user.is_admin:
        raise HTTPException(status_code=403, detail="Only the owner can edit")
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
        else:
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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.kind == KIND_DM:
        raise HTTPException(status_code=400, detail="Not applicable to direct messages")
    await require_membership(db, channel_id, user.id)

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
    ch = await db.get(Channel, channel_id)
    if ch is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    if ch.kind == KIND_DM:
        raise HTTPException(status_code=400, detail="Not applicable to direct messages")
    if body.role not in (ROLE_OWNER, ROLE_MOD, ROLE_MEMBER):
        raise HTTPException(
            status_code=400, detail="Role must be 'owner', 'mod', or 'member'"
        )

    # Only the channel owner or a site admin can assign roles.
    if not user.is_admin:
        me = await _target_member(db, channel_id, user.id)
        if me is None or me.role != ROLE_OWNER:
            raise HTTPException(
                status_code=403,
                detail="Only the channel owner or a site admin can set roles",
            )
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
    if not user.is_admin:
        member = await _target_member(db, channel_id, user.id)
        if member is None or member.role != ROLE_OWNER:
            raise HTTPException(
                status_code=403,
                detail="Only the channel owner or a site admin can delete this channel",
            )

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
