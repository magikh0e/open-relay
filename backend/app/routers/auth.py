from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import func, or_, select

from ..audit import client_ip
from ..config import settings
from ..deps import DB
from ..models import Channel, ChannelMember, ROLE_MEMBER, User
from ..redis_client import rate_limit_hit
from ..schemas import LoginIn, RefreshIn, RegisterIn, TokenPair, UserOut
from ..seed import WHATSNEW_SLUG
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token_claims,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenPair, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, request: Request, db: DB) -> TokenPair:
    # Signup is open, so throttle per IP — otherwise one script can mint
    # unlimited accounts (each of which also lands in #whatsnew).
    if await rate_limit_hit(
        f"register:ip:{client_ip(request)}", settings.register_rate_per_hour, 3600
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many accounts created from here. Try again later.",
        )
    exists = (
        await db.execute(
            select(User).where(
                or_(
                    func.lower(User.username) == body.username.lower(),
                    func.lower(User.email) == body.email.lower(),
                )
            )
        )
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username or email already taken",
        )
    user = User(
        username=body.username,
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name or body.username,
    )
    db.add(user)
    await db.flush()
    # Auto-join the read-only #whatsnew announcement channel so updates reach
    # everyone without a manual join.
    wn = (
        await db.execute(select(Channel).where(Channel.slug == WHATSNEW_SLUG))
    ).scalar_one_or_none()
    if wn is not None:
        db.add(
            ChannelMember(channel_id=wn.id, user_id=user.id, role=ROLE_MEMBER)
        )
    await db.commit()
    return TokenPair(
        access_token=create_access_token(user.id, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


@router.post("/login", response_model=TokenPair)
async def login(body: LoginIn, request: Request, db: DB) -> TokenPair:
    ident = body.username_or_email.strip().lower()

    # Brute-force throttle: tight per-identifier limit, looser per-IP limit.
    limit = settings.login_rate_per_min
    ip = client_ip(request)
    if await rate_limit_hit(f"login:id:{ident}", limit, 60) or await rate_limit_hit(
        f"login:ip:{ip}", limit * 3, 60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please wait a minute and try again.",
        )

    user = (
        await db.execute(
            select(User).where(
                or_(
                    func.lower(User.username) == ident,
                    func.lower(User.email) == ident,
                )
            )
        )
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account disabled"
        )
    # Transparent hash upgrade if argon2 params changed.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await db.commit()
    return TokenPair(
        access_token=create_access_token(user.id, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


@router.post("/refresh", response_model=TokenPair)
async def refresh(body: RefreshIn, db: DB) -> TokenPair:
    claims = decode_token_claims(body.refresh_token, expected_type="refresh")
    user_id = claims[0] if claims else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found"
        )
    # Without this a revoked refresh token could still mint fresh access
    # tokens, which would defeat the whole point of revocation.
    if claims[1] != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired — please sign in again",
        )
    return TokenPair(
        access_token=create_access_token(user.id, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )
