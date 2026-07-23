from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from ..deps import DB, CurrentUser
from ..models import ChannelMember, User
from ..redis_client import (
    away_map,
    clear_away,
    online_user_ids,
    rate_limit_hit,
    set_away,
)
from ..sanitize import sanitize_text
from ..schemas import (
    AwayIn,
    PasswordChange,
    PrivacySettings,
    ProfileOut,
    ProfileUpdate,
    UserOut,
    UserPublic,
)
from ..security import hash_password, verify_password
from ..ws_manager import manager

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        avatar_url=user.avatar_url,
        is_admin=user.is_admin,
        has_password=bool(user.password_hash),
        share_typing=user.share_typing,
        share_presence=user.share_presence,
        allow_dms=user.allow_dms,
        discoverable=user.discoverable,
    )


@router.get("/me/settings", response_model=PrivacySettings)
async def get_privacy(user: CurrentUser) -> PrivacySettings:
    return PrivacySettings.model_validate(user)


@router.patch("/me/settings", response_model=PrivacySettings)
async def update_privacy(
    body: PrivacySettings, db: DB, user: CurrentUser
) -> PrivacySettings:
    """Update privacy preferences. Every one of these is also enforced on the
    server, so turning a signal off actually stops it being produced rather
    than just hiding it in this client."""
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(user, field, value)
    await db.commit()
    return PrivacySettings.model_validate(user)


@router.patch("/me", response_model=ProfileOut)
async def update_me(body: ProfileUpdate, db: DB, user: CurrentUser) -> User:
    """Update the current user's own profile. All free-text is sanitized."""
    if body.display_name is not None:
        cleaned = sanitize_text(body.display_name, max_length=64)
        if not cleaned:
            raise HTTPException(status_code=422, detail="Display name cannot be empty")
        user.display_name = cleaned
    if body.bio is not None:
        user.bio = sanitize_text(body.bio, max_length=500, allow_newlines=True)
    if body.pronouns is not None:
        user.pronouns = sanitize_text(body.pronouns, max_length=40)
    await db.commit()
    return user


@router.post("/me/password", status_code=204)
async def change_password(body: PasswordChange, db: DB, user: CurrentUser) -> None:
    """Change (or, for SSO-only accounts, first set) your own password."""
    # Confirming the current password is a guessable secret, so throttle it the
    # way login is throttled rather than allowing unlimited attempts.
    if await rate_limit_hit(f"rl:pw:{user.id}", 5, 300):
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — wait a few minutes and try again.",
        )

    if user.password_hash:
        if not body.current_password or not verify_password(
            body.current_password, user.password_hash
        ):
            raise HTTPException(
                status_code=403, detail="Current password is incorrect"
            )
        if body.current_password == body.new_password:
            raise HTTPException(
                status_code=422,
                detail="New password must be different from the current one",
            )
    # Accounts with no password (signed up via SSO) are setting one for the
    # first time; the authenticated session is the proof of identity.

    user.password_hash = hash_password(body.new_password)
    await db.commit()


@router.get("/search", response_model=list[UserPublic])
async def search(q: str, db: DB, user: CurrentUser) -> list[User]:
    q = q.strip()
    if len(q) < 2:
        return []
    like = f"%{q}%"
    rows = (
        await db.execute(
            select(User)
            .where(
                User.is_active.is_(True),
                User.id != user.id,
                # Opted out of being found by search. They remain visible in
                # channels they share with you — this only hides them from
                # strangers looking them up.
                User.discoverable.is_(True),
                or_(User.username.ilike(like), User.display_name.ilike(like)),
            )
            .limit(20)
        )
    ).scalars().all()
    return list(rows)


@router.get("/online", response_model=list[str])
async def online(user: CurrentUser) -> list[str]:
    """Return the set of currently-online user ids."""
    return list(await online_user_ids())


@router.get("/away")
async def away(user: CurrentUser) -> dict[str, str]:
    """Map of user_id -> away message for everyone currently away."""
    return await away_map()


@router.post("/away", status_code=204)
async def set_away_status(body: AwayIn, db: DB, user: CurrentUser) -> None:
    msg = sanitize_text(body.message or "", max_length=140)
    if msg:
        await set_away(user.id, msg)
    else:
        await clear_away(user.id)
    # Notify every channel the user is in so member lists update.
    channel_ids = (
        await db.execute(
            select(ChannelMember.channel_id).where(
                ChannelMember.user_id == user.id
            )
        )
    ).scalars().all()
    for cid in channel_ids:
        await manager.publish_room(
            cid,
            {
                "type": "away",
                "data": {
                    "user_id": user.id,
                    "away": bool(msg),
                    "message": msg,
                },
            },
        )


# NOTE: declared last so the fixed paths above (/me, /search, /online) are not
# shadowed by the {user_id} path parameter.
@router.get("/{user_id}", response_model=ProfileOut)
async def profile(user_id: str, db: DB, viewer: CurrentUser) -> User:
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    return target
