"""Bot accounts.

A bot is a user with `is_bot` set: it reuses display names, avatars, channel
membership, mentions and presence rather than duplicating any of it. What
differs is how it authenticates and what it may do.

- It holds a long-lived opaque token instead of a JWT, because a program is
  meant to stay connected for weeks and a 30-minute token with a refresh dance
  is the wrong shape for that.
- The token is stored as a SHA-256 digest and shown exactly once.
- It carries scopes (read, write, react) and is refused anything outside them.
- It sees only the channels it has been added to, through ordinary membership,
  so "which bots can read this channel" is answered by the member list.

Creation is admin-only for now. A bot is a durable identity on the server, and
letting any account mint them invites junk. That is easy to relax later and
awkward to tighten.

A bot has no encryption keypair, since there is no passphrase for a program to
hold. DMs with one are therefore plaintext, and a group containing one cannot
be encrypted: publishing a group key requires every member to have published a
public key, which enforces itself without special-casing.
"""
import hashlib
import secrets

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import DB, CurrentUser
from ..models import BOT_SCOPES, BotToken, User
from ..sanitize import sanitize_text, validate_username
from ..schemas import BotCreate, BotCreated, BotOut

router = APIRouter(prefix="/bots", tags=["bots"])


def _require_admin(user: User) -> None:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only a site admin can manage bot accounts",
        )


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _out(bot: User, token: BotToken | None) -> BotOut:
    return BotOut(
        id=bot.id,
        username=bot.username,
        display_name=bot.display_name,
        scopes=[s for s in (token.scopes.split(",") if token else []) if s],
        created_at=bot.created_at,
        last_used_at=token.last_used_at if token else None,
    )


@router.get("", response_model=list[BotOut])
async def list_bots(db: DB, user: CurrentUser) -> list[BotOut]:
    _require_admin(user)
    bots = (
        await db.execute(
            select(User).where(User.is_bot.is_(True), User.is_active.is_(True))
        )
    ).scalars().all()
    tokens = {
        t.user_id: t
        for t in (
            await db.execute(
                select(BotToken).where(BotToken.user_id.in_([b.id for b in bots]))
            )
        ).scalars().all()
    } if bots else {}
    return [_out(b, tokens.get(b.id)) for b in bots]


@router.post("", response_model=BotCreated, status_code=status.HTTP_201_CREATED)
async def create_bot(body: BotCreate, db: DB, user: CurrentUser) -> BotCreated:
    """Mint a bot and its token. The token is returned once and never again."""
    _require_admin(user)

    username = validate_username(body.username)
    clash = (
        await db.execute(select(User).where(User.username == username))
    ).scalar_one_or_none()
    if clash is not None:
        raise HTTPException(status_code=409, detail="That username is taken")

    bad = [s for s in body.scopes if s not in BOT_SCOPES]
    if bad:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown scope(s): {', '.join(bad)}. Valid: {', '.join(BOT_SCOPES)}",
        )

    bot = User(
        username=username,
        # Bots need a unique email for the column's constraint but have no
        # inbox; the .invalid TLD is reserved precisely so it can never resolve.
        email=f"{username}@bot.invalid",
        # No password hash at all, so the login and OAuth paths cannot admit it
        # even if the bot flag were somehow cleared.
        password_hash=None,
        display_name=sanitize_text(body.display_name or username, max_length=64),
        is_bot=True,
        # A program has nothing to say about typing or presence privacy, and
        # should not turn up in searches for people.
        discoverable=False,
        share_typing=False,
    )
    db.add(bot)
    await db.flush()

    raw = secrets.token_urlsafe(32)
    token = BotToken(
        user_id=bot.id,
        token_hash=_hash(raw),
        scopes=",".join(body.scopes),
        created_by=user.id,
    )
    db.add(token)
    await db.commit()

    return BotCreated(**_out(bot, token).model_dump(), token=raw)


@router.post("/{bot_id}/token", response_model=BotCreated)
async def rotate_token(bot_id: str, db: DB, user: CurrentUser) -> BotCreated:
    """Replace a bot's token, keeping its identity and channel memberships."""
    _require_admin(user)
    bot = await db.get(User, bot_id)
    if bot is None or not bot.is_bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    old = (
        await db.execute(select(BotToken).where(BotToken.user_id == bot_id))
    ).scalars().all()
    scopes = old[0].scopes if old else ""
    for t in old:
        await db.delete(t)

    raw = secrets.token_urlsafe(32)
    token = BotToken(
        user_id=bot.id, token_hash=_hash(raw), scopes=scopes, created_by=user.id
    )
    db.add(token)
    await db.commit()
    return BotCreated(**_out(bot, token).model_dump(), token=raw)


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot(bot_id: str, db: DB, user: CurrentUser) -> None:
    """Remove a bot entirely. Its messages survive with the sender nulled, the
    same as for a deleted person, so conversations do not develop holes."""
    _require_admin(user)
    bot = await db.get(User, bot_id)
    if bot is None or not bot.is_bot:
        raise HTTPException(status_code=404, detail="Bot not found")
    await db.delete(bot)
    await db.commit()
