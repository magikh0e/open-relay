import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models import Ban, BotToken, ChannelMember, User
from .security import decode_token_claims

_bearer = HTTPBearer(auto_error=False)


async def get_user_by_id(db: AsyncSession, user_id: str) -> User | None:
    return await db.get(User, user_id)


async def resolve_token_user(db: AsyncSession, token: str) -> User | None:
    """Shared by HTTP deps and the WebSocket handshake."""
    claims = decode_token_claims(token, expected_type="access")
    if not claims:
        return None
    user_id, tv = claims
    user = await get_user_by_id(db, user_id)
    if not user or not user.is_active:
        return None
    # Token issued before the user's credentials changed — treat as revoked.
    if tv != user.token_version:
        return None
    # Ban is keyed by its own id, so look it up by user_id explicitly.
    banned = (
        await db.execute(select(Ban).where(Ban.user_id == user_id))
    ).scalar_one_or_none()
    if banned:
        return None
    return user


async def resolve_bot_token(db: AsyncSession, token: str) -> User | None:
    """Resolve a bot's long-lived token.

    Bots hold an opaque token rather than a JWT: a program is meant to stay
    connected for weeks, and a 30-minute token with a refresh dance is the
    wrong shape for that. Only the SHA-256 digest is stored, so what is in the
    database cannot be replayed as a credential.

    The granted scopes are attached to the returned user for the request. They
    are set on the instance rather than persisted, so nothing writes them back.
    """
    digest = hashlib.sha256(token.encode()).hexdigest()
    row = (
        await db.execute(select(BotToken).where(BotToken.token_hash == digest))
    ).scalar_one_or_none()
    if row is None:
        return None
    user = await db.get(User, row.user_id)
    # is_bot is checked as well as is_active: clearing the flag should retire
    # the token, not leave it working against a now-human account.
    if user is None or not user.is_active or not user.is_bot:
        return None
    user.bot_scopes = [s for s in row.scopes.split(",") if s]
    row.last_used_at = datetime.now(timezone.utc)
    await db.commit()
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
        # Not a valid JWT, so it may be a bot token. Tried second so the common
        # path is unaffected.
        user = await resolve_bot_token(db, creds.credentials)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]


def bot_scopes(user: User) -> list[str]:
    """Scopes granted to this request, empty for a human (who is not limited)."""
    return getattr(user, "bot_scopes", []) or []


def require_scope(scope: str):
    """Gate an endpoint on a bot scope.

    People pass straight through: scopes narrow what a *program* may do on
    someone's server, and are not a permission system for humans, who are
    already governed by membership and roles.
    """

    async def dep(user: CurrentUser) -> User:
        if user.is_bot and scope not in bot_scopes(user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This bot does not have the '{scope}' scope",
            )
        return user

    return dep


async def reject_bots(user: CurrentUser) -> User:
    """Refuse a bot outright.

    For anything that only makes sense for a person: changing a password,
    deleting the account, publishing encryption keys, minting invites. A bot
    reaching one of these is a bug or an attack, never a legitimate use.
    """
    if user.is_bot:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not available to bot accounts",
        )
    return user


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
