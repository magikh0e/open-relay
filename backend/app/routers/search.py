from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import DB, CurrentUser
from ..models import KIND_DM, Channel, ChannelMember, Message, User
from ..schemas import SearchResult, UserPublic

router = APIRouter(prefix="/search", tags=["search"])


@router.get("", response_model=list[SearchResult])
async def search(
    q: str,
    db: DB,
    user: CurrentUser,
    limit: int = Query(default=30, ge=1, le=100),
) -> list[SearchResult]:
    """Substring search over messages in channels the user belongs to."""
    q = q.strip()
    if len(q) < 2:
        return []

    my_channels = select(ChannelMember.channel_id).where(
        ChannelMember.user_id == user.id
    )
    rows = (
        await db.execute(
            select(Message)
            .options(selectinload(Message.sender))
            .where(
                Message.channel_id.in_(my_channels),
                Message.deleted_at.is_(None),
                Message.content.ilike(f"%{q}%"),
            )
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    if not rows:
        return []

    channel_ids = {m.channel_id for m in rows}
    channels = {
        c.id: c
        for c in (
            await db.execute(select(Channel).where(Channel.id.in_(channel_ids)))
        ).scalars().all()
    }

    # Resolve DM display names (the other participant).
    dm_names: dict[str, str] = {}
    dm_ids = [cid for cid, c in channels.items() if c.kind == KIND_DM]
    if dm_ids:
        drows = (
            await db.execute(
                select(ChannelMember.channel_id, User)
                .join(User, User.id == ChannelMember.user_id)
                .where(
                    ChannelMember.channel_id.in_(dm_ids),
                    ChannelMember.user_id != user.id,
                )
            )
        ).all()
        for cid, u in drows:
            dm_names[cid] = u.display_name

    def channel_name(c: Channel) -> str:
        if c.kind == KIND_DM:
            return dm_names.get(c.id, "Direct message")
        return c.name

    return [
        SearchResult(
            id=m.id,
            channel_id=m.channel_id,
            channel_name=channel_name(channels[m.channel_id]),
            channel_kind=channels[m.channel_id].kind,
            sender=UserPublic.model_validate(m.sender) if m.sender else None,
            content=m.content,
            created_at=m.created_at,
            thread_root_id=m.thread_root_id,
        )
        for m in rows
        if m.channel_id in channels
    ]
