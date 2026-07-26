import hashlib
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
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
    request: Request,
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
    if user.is_bot:
        authorise_bot(request, user)
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
DB = Annotated[AsyncSession, Depends(get_db)]


# Everything a bot may reach, and the scope each needs. An empty string means
# any bot may call it regardless of scopes.
#
# This is an allowlist on purpose. Adding a check to the handful of endpoints a
# bot obviously needs would leave every other endpoint open by omission, and
# the list of things a program must never touch is far longer and grows every
# time somebody adds a route. Deny by default means a new endpoint is closed to
# bots until someone deliberately opens it, and it makes a bot's whole reach
# one table you can read in ten seconds.
BOT_ROUTES: dict[tuple[str, str], str] = {
    # Identity. Scope-free so even a write-only bot can learn its own id.
    ("GET", "/users/me"): "",
    # Reading
    ("GET", "/channels"): "read",
    ("GET", "/channels/{channel_id}"): "read",
    ("GET", "/channels/{channel_id}/members"): "read",
    ("GET", "/channels/{channel_id}/messages"): "read",
    ("GET", "/channels/{channel_id}/messages/{root_id}/thread"): "read",
    ("GET", "/users/{user_id}"): "read",
    # Marking read is bookkeeping about what it has already seen.
    ("POST", "/channels/{channel_id}/read"): "read",
    # Writing, including correcting or withdrawing its own messages.
    ("POST", "/channels/{channel_id}/messages"): "write",
    ("PATCH", "/channels/{channel_id}/messages/{message_id}"): "write",
    ("DELETE", "/channels/{channel_id}/messages/{message_id}"): "write",
    # Reacting
    ("POST", "/channels/{channel_id}/messages/{message_id}/reactions"): "react",
}


def bot_scopes(user: User) -> list[str]:
    """Scopes granted to this request, empty for a human (who is not limited)."""
    return getattr(user, "bot_scopes", []) or []


def authorise_bot(request: Request, user: User) -> None:
    """Refuse a bot any endpoint it has not been explicitly granted.

    Runs where bots are authenticated, so every authenticated route passes
    through it and none can be forgotten. Unauthenticated routes are unaffected,
    since a bot has no identity there to act on.
    """
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    needed = BOT_ROUTES.get((request.method, path))
    if needed is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Bot accounts cannot use this endpoint",
        )
    if needed and needed not in bot_scopes(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"This bot does not have the '{needed}' scope",
        )


async def reject_bots(user: CurrentUser) -> User:
    """Refuse a bot outright, on the endpoints where it matters most.

    Redundant with BOT_ROUTES, which already denies anything unlisted, and kept
    deliberately: if one of these were ever added to that table by mistake, an
    explicit guard on password changes and key publishing still refuses. Cheap
    insurance on the two or three routes where being wrong is expensive.
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
