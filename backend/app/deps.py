from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Ban, ChannelMember, User
from .security import decode_token

_bearer = HTTPBearer(auto_error=False)


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def resolve_token_user(db: AsyncSession, token: str) -> User | None:
    """Shared by HTTP deps and the WebSocket handshake."""
    user_id = decode_token(token, expected_type="access")
    if not user_id:
        return None
    user = await get_user_by_id(db, user_id)
    if not user or not user.is_active:
        return None
    # Ban is keyed by its own id, so look it up by user_id explicitly.
    banned = (
        await db.execute(select(Ban).where(Ban.user_id == user_id))
    ).scalar_one_or_none()
    if banned:
        return None
    return user


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated"
        )
    user = await resolve_token_user(db, creds.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]


async def require_membership(
    db: AsyncSession, channel_id: str, user_id: str
) -> ChannelMember:
    member = (
        await db.execute(
            select(ChannelMember).where(
                ChannelMember.channel_id == channel_id,
                ChannelMember.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a member of this channel",
        )
    return member
