from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DB, CurrentUser
from ..models import (
    KIND_DM,
    ROLE_MEMBER,
    Channel,
    ChannelMember,
    User,
)
from ..schemas import ChannelOut, DMCreate, UserPublic
from ..ws_manager import manager

router = APIRouter(prefix="/dms", tags=["dms"])


def _dm_key(a: str, b: str) -> str:
    """Canonical, order-independent key so each pair has exactly one DM."""
    lo, hi = sorted((a, b))
    return f"{lo}:{hi}"


@router.get("", response_model=list[ChannelOut])
async def list_dms(db: DB, user: CurrentUser) -> list[ChannelOut]:
    """List the user's DM channels, annotated with the other participant."""
    channels = (
        await db.execute(
            select(Channel)
            .join(ChannelMember, ChannelMember.channel_id == Channel.id)
            .where(
                Channel.kind == KIND_DM,
                ChannelMember.user_id == user.id,
                ChannelMember.hidden.is_(False),
            )
            .order_by(Channel.created_at.desc())
        )
    ).scalars().all()

    out: list[ChannelOut] = []
    for ch in channels:
        other = (
            await db.execute(
                select(User)
                .join(ChannelMember, ChannelMember.user_id == User.id)
                .where(
                    ChannelMember.channel_id == ch.id, User.id != user.id
                )
            )
        ).scalar_one_or_none()
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
