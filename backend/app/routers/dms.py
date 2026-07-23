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
            .where(Channel.kind == KIND_DM, ChannelMember.user_id == user.id)
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
