from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from ..deps import DB, CurrentUser
from ..models import (
    KIND_DM,
    KIND_GROUP,
    ROLE_MEMBER,
    ROLE_OWNER,
    MAX_GROUP_SIZE,
    Channel,
    ChannelMember,
    GroupKey,
    GroupKeyShare,
    User,
    UserKey,
)
from ..schemas import (
    ChannelOut,
    DMCreate,
    GroupCreate,
    GroupKeyOut,
    GroupKeysOut,
    GroupRekeyIn,
    UserPublic,
)
from ..ws_manager import manager
from .channels import _announce_removal, _channel_stats, _member_count
from .messages import announce_action

router = APIRouter(prefix="/dms", tags=["dms"])


def _dm_key(a: str, b: str) -> str:
    """Canonical, order-independent key so each pair has exactly one DM."""
    lo, hi = sorted((a, b))
    return f"{lo}:{hi}"


@router.get("", response_model=list[ChannelOut])
async def list_dms(db: DB, user: CurrentUser) -> list[ChannelOut]:
    """List the user's direct-message and group channels.

    A 1:1 DM is annotated with the other participant; a group carries its own
    name and real member count.
    """
    channels = (
        await db.execute(
            select(Channel)
            .join(ChannelMember, ChannelMember.channel_id == Channel.id)
            .where(
                Channel.kind.in_([KIND_DM, KIND_GROUP]),
                ChannelMember.user_id == user.id,
                ChannelMember.hidden.is_(False),
            )
            .order_by(Channel.created_at.desc())
        )
    ).scalars().all()

    ids = [ch.id for ch in channels]
    stats = await _channel_stats(db, ids, user.id)

    # The other participant of every 1:1 DM, in one query rather than per-DM.
    dm_ids = [ch.id for ch in channels if ch.kind == KIND_DM]
    peers: dict[str, User] = {}
    if dm_ids:
        for cid, other in (
            await db.execute(
                select(ChannelMember.channel_id, User)
                .join(User, User.id == ChannelMember.user_id)
                .where(
                    ChannelMember.channel_id.in_(dm_ids),
                    ChannelMember.user_id != user.id,
                )
            )
        ).all():
            peers[cid] = other

    out: list[ChannelOut] = []
    for ch in channels:
        st = stats[ch.id]
        if ch.kind == KIND_GROUP:
            out.append(
                ChannelOut(
                    id=ch.id,
                    kind=ch.kind,
                    slug=None,
                    name=ch.name or "Group",
                    topic="",
                    created_by=ch.created_by,
                    created_at=ch.created_at,
                    member_count=st["member_count"],
                    is_member=True,
                    unread_count=st["unread"],
                    mention_count=st["mentions"],
                )
            )
            continue
        other = peers.get(ch.id)
        out.append(
            ChannelOut(
                id=ch.id,
                kind=ch.kind,
                slug=None,
                name=other.display_name if other else "Unknown",
                topic=other.username if other else "",
                created_by=ch.created_by,
                created_at=ch.created_at,
                member_count=2,
                is_member=True,
                unread_count=st["unread"],
                mention_count=st["mentions"],
            )
        )
    return out


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def open_dm(body: DMCreate, db: DB, user: CurrentUser) -> ChannelOut:
    if body.user_id == user.id:
        raise HTTPException(status_code=400, detail="Cannot DM yourself")
    other = await db.get(User, body.user_id)
    if other is None or not other.is_active:
        raise HTTPException(status_code=404, detail="User not found")

    key = _dm_key(user.id, other.id)
    existing = (
        await db.execute(select(Channel).where(Channel.dm_key == key))
    ).scalar_one_or_none()

    if existing is None:
        # Only gate *new* conversations: someone who already has a DM with you
        # can still use it, and admins can always reach people.
        if not other.allow_dms and not user.is_admin:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"{other.display_name} isn't accepting new direct messages.",
            )
        ch = Channel(kind=KIND_DM, dm_key=key, created_by=user.id, name="")
        db.add(ch)
        await db.flush()
        db.add(ChannelMember(channel_id=ch.id, user_id=user.id, role=ROLE_MEMBER))
        db.add(ChannelMember(channel_id=ch.id, user_id=other.id, role=ROLE_MEMBER))
        await db.commit()
        # Notify the other user's open sessions that a new DM exists.
        await manager.publish_user(
            other.id, {"type": "dm_opened", "data": {"channel_id": ch.id}}
        )
    else:
        ch = existing
        # Reopening a DM you'd closed brings it back to your sidebar.
        await db.execute(
            ChannelMember.__table__.update()
            .where(
                ChannelMember.channel_id == ch.id,
                ChannelMember.user_id == user.id,
            )
            .values(hidden=False)
        )
        await db.commit()

    return ChannelOut(
        id=ch.id,
        kind=ch.kind,
        slug=None,
        name=other.display_name,
        topic=other.username,
        created_by=ch.created_by,
        created_at=ch.created_at,
        member_count=2,
        is_member=True,
    )


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def close_dm(channel_id: str, db: DB, user: CurrentUser) -> None:
    """Close a DM: hides it from your sidebar only.

    Nothing is deleted and the other person is unaffected — the conversation
    reappears for you if either of you sends a new message, or if you open it
    again from their profile.
    """
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.kind != KIND_DM:
        raise HTTPException(status_code=404, detail="Direct message not found")
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Direct message not found")
    member.hidden = True
    await db.commit()


# --- Group DMs -----------------------------------------------------------
# A group DM is a private channel (KIND_GROUP) with a member set, surfaced in
# the DM list. Posting, reading, threads, files, mentions and presence all reuse
# the channel/message stack; only membership management lives here. Groups are
# plaintext (server-readable), like channels; E2EE stays 1:1 for now.


def _group_out(ch: Channel, member_count: int) -> ChannelOut:
    return ChannelOut(
        id=ch.id,
        kind=ch.kind,
        slug=None,
        name=ch.name or "Group",
        topic="",
        created_by=ch.created_by,
        created_at=ch.created_at,
        member_count=member_count,
        is_member=True,
    )


def _default_group_name(users: list[User]) -> str:
    return ", ".join(u.display_name for u in users)[:64] or "Group"


async def _require_group_owner(db, channel_id: str, user: User) -> Channel:
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.kind != KIND_GROUP:
        raise HTTPException(status_code=404, detail="Group not found")
    if user.is_admin:
        return ch
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user.id,
            )
        )
    ).scalar_one_or_none()
    if member is None or member.role != ROLE_OWNER:
        raise HTTPException(
            status_code=403, detail="Only the group's owner can do that"
        )
    return ch


@router.post(
    "/group", response_model=ChannelOut, status_code=status.HTTP_201_CREATED
)
async def create_group(body: GroupCreate, db: DB, user: CurrentUser) -> ChannelOut:
    # Dedupe and drop the creator if they listed themselves.
    ids = [uid for uid in dict.fromkeys(body.user_ids) if uid != user.id]
    if len(ids) < 2:
        raise HTTPException(
            status_code=400, detail="A group needs at least 2 other people"
        )
    if len(ids) + 1 > MAX_GROUP_SIZE:
        raise HTTPException(
            status_code=400, detail=f"A group can hold at most {MAX_GROUP_SIZE} people"
        )
    users = (
        await db.execute(
            select(User).where(User.id.in_(ids), User.is_active.is_(True))
        )
    ).scalars().all()
    if len(users) != len(ids):
        raise HTTPException(status_code=404, detail="One or more people not found")
    if not user.is_admin:
        blocked = [u.display_name for u in users if not u.allow_dms]
        if blocked:
            raise HTTPException(
                status_code=403,
                detail=f"Not accepting new messages: {', '.join(blocked)}",
            )

    name = (body.name or "").strip()[:64] or _default_group_name(users)
    ch = Channel(kind=KIND_GROUP, dm_key=None, created_by=user.id, name=name)
    db.add(ch)
    await db.flush()
    db.add(ChannelMember(channel_id=ch.id, user_id=user.id, role=ROLE_OWNER))
    for u in users:
        db.add(ChannelMember(channel_id=ch.id, user_id=u.id, role=ROLE_MEMBER))
    await db.commit()
    for u in users:
        await manager.publish_user(
            u.id, {"type": "dm_opened", "data": {"channel_id": ch.id}}
        )
    return _group_out(ch, len(users) + 1)


@router.post("/{channel_id}/members", status_code=status.HTTP_204_NO_CONTENT)
async def add_group_member(
    channel_id: str, body: DMCreate, db: DB, user: CurrentUser
) -> None:
    await _require_group_owner(db, channel_id, user)
    target = await db.get(User, body.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    already = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == target.id,
            )
        )
    ).scalar_one_or_none()
    if already is not None:
        return  # idempotent
    if not target.allow_dms and not user.is_admin:
        raise HTTPException(
            status_code=403,
            detail=f"{target.display_name} isn't accepting new messages.",
        )
    if await _member_count(db, channel_id) >= MAX_GROUP_SIZE:
        raise HTTPException(status_code=400, detail="This group is full")
    db.add(
        ChannelMember(channel_id=channel_id, user_id=target.id, role=ROLE_MEMBER)
    )
    await announce_action(
        db, channel_id, user, f"added @{target.username} to the group"
    )
    await manager.publish_user(
        target.id, {"type": "channel_added", "data": {"channel_id": channel_id}}
    )


# --- Group encryption keys ------------------------------------------------
# A group is encrypted under a symmetric key that the server never sees. The
# publisher seals one copy per member under the pairwise ECDH secret they
# already share, so the server stores only blobs it cannot open. Each new epoch
# supersedes the last; messages record the epoch that encrypted them.


async def _member_ids(db, channel_id: str) -> set[str]:
    rows = (
        await db.execute(
            select(ChannelMember.user_id).where(
                ChannelMember.channel_id == channel_id
            )
        )
    ).scalars().all()
    return set(rows)


@router.post(
    "/{channel_id}/keys", response_model=GroupKeysOut, status_code=status.HTTP_201_CREATED
)
async def publish_group_key(
    channel_id: str, body: GroupRekeyIn, db: DB, user: CurrentUser
) -> GroupKeysOut:
    """Publish a new key epoch for a group, with a share for every member.

    Owner only, because it is the same authority as changing who is in the
    group: whoever hands out the key decides who can read.
    """
    await _require_group_owner(db, channel_id, user)

    members = await _member_ids(db, channel_id)
    shared_with = {s.user_id for s in body.shares}
    if len(shared_with) != len(body.shares):
        raise HTTPException(status_code=400, detail="Duplicate share for a member")
    # An exact match matters in both directions: a missing share locks that
    # member out of the conversation, and a stray one would be a share for
    # somebody who is not in the group.
    if shared_with != members:
        missing = len(members - shared_with)
        extra = len(shared_with - members)
        raise HTTPException(
            status_code=400,
            detail=(
                "Shares must cover exactly the current members "
                f"({missing} missing, {extra} not in the group)"
            ),
        )

    # Everyone needs a published public key, or they could not have been sealed
    # a share in the first place. Checked explicitly so the failure is a clear
    # 400 rather than a member who silently cannot read anything.
    with_keys = set(
        (
            await db.execute(
                select(UserKey.user_id).where(UserKey.user_id.in_(members))
            )
        ).scalars().all()
    )
    if with_keys != members:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{len(members - with_keys)} member(s) have not set up encryption yet"
            ),
        )

    # The server assigns the epoch rather than trusting the client, so racing
    # publishers cannot collide on a number or replay an old one.
    prev = (
        await db.execute(
            select(func.max(GroupKey.epoch)).where(GroupKey.channel_id == channel_id)
        )
    ).scalar_one_or_none()
    epoch = (prev or 0) + 1

    gk = GroupKey(channel_id=channel_id, epoch=epoch, created_by=user.id)
    db.add(gk)
    await db.flush()
    for s in body.shares:
        db.add(
            GroupKeyShare(
                group_key_id=gk.id,
                user_id=s.user_id,
                wrapped_key=s.wrapped_key,
                sender_public_key=s.sender_public_key,
            )
        )
    await db.commit()

    # Tell the other members a new epoch exists so they can fetch their share
    # without waiting for a reload.
    await manager.publish_room(
        channel_id,
        {"type": "group_key", "data": {"channel_id": channel_id, "epoch": epoch}},
    )
    return await _my_group_keys(db, channel_id, user.id)


@router.get("/{channel_id}/keys", response_model=GroupKeysOut)
async def group_keys(channel_id: str, db: DB, user: CurrentUser) -> GroupKeysOut:
    """Every epoch the caller can open, plus the epoch now in use.

    A member added after an epoch was published holds no share for it and so
    cannot read that stretch of history, which is the intended behaviour.
    """
    ch = await db.get(Channel, channel_id)
    if ch is None or ch.kind != KIND_GROUP:
        raise HTTPException(status_code=404, detail="Group not found")
    if user.id not in await _member_ids(db, channel_id):
        raise HTTPException(status_code=403, detail="You are not a member of this group")
    return await _my_group_keys(db, channel_id, user.id)


async def _my_group_keys(db, channel_id: str, user_id: str) -> GroupKeysOut:
    rows = (
        await db.execute(
            select(GroupKey.epoch, GroupKeyShare.wrapped_key, GroupKeyShare.sender_public_key)
            .join(GroupKeyShare, GroupKeyShare.group_key_id == GroupKey.id)
            .where(GroupKey.channel_id == channel_id, GroupKeyShare.user_id == user_id)
            .order_by(GroupKey.epoch)
        )
    ).all()
    current = (
        await db.execute(
            select(func.max(GroupKey.epoch)).where(GroupKey.channel_id == channel_id)
        )
    ).scalar_one_or_none()
    return GroupKeysOut(
        keys=[
            GroupKeyOut(epoch=e, wrapped_key=w, sender_public_key=p) for e, w, p in rows
        ],
        current_epoch=current,
    )


@router.delete(
    "/{channel_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_group_member(
    channel_id: str, user_id: str, db: DB, user: CurrentUser
) -> None:
    await _require_group_owner(db, channel_id, user)
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Use leave to remove yourself")
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Not a member")
    target = await db.get(User, user_id)
    await db.delete(member)
    if target is not None:
        await announce_action(
            db, channel_id, user, f"removed @{target.username} from the group"
        )
    else:
        await db.commit()
    await _announce_removal(channel_id, user_id, banned=False)
