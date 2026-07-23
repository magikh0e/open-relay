from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from ..deps import DB, CurrentUser
from ..models import User
from ..redis_client import online_user_ids
from ..sanitize import sanitize_text
from ..schemas import ProfileOut, ProfileUpdate, UserOut, UserPublic

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> User:
    return user


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


# NOTE: declared last so the fixed paths above (/me, /search, /online) are not
# shadowed by the {user_id} path parameter.
@router.get("/{user_id}", response_model=ProfileOut)
async def profile(user_id: str, db: DB, viewer: CurrentUser) -> User:
    target = await db.get(User, user_id)
    if target is None or not target.is_active:
        raise HTTPException(status_code=404, detail="User not found")
    return target
