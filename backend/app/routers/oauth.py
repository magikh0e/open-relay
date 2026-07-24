import re
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from ..config import settings
from ..deps import DB
from ..models import OAuthAccount, User
from ..oauth import (
    enabled_providers,
    fetch_userinfo,
    is_enabled,
    normalize,
    provider_conf,
    redirect_uri,
    save_state,
    take_state,
)
from ..sanitize import sanitize_text
from ..security import create_access_token, create_refresh_token

router = APIRouter(prefix="/auth/oauth", tags=["oauth"])


@router.get("/providers")
async def providers() -> list[str]:
    """Which SSO providers are configured (so the UI shows only those)."""
    return enabled_providers()


@router.get("/{provider}/start")
async def start(provider: str):
    if not is_enabled(provider):
        raise HTTPException(status_code=404, detail="Provider not enabled")
    conf = provider_conf(provider)
    state = secrets.token_urlsafe(24)
    await save_state(state, provider)
    params = {
        "client_id": conf["client_id"],
        "redirect_uri": redirect_uri(provider),
        "response_type": "code",
        "scope": conf["scope"],
        "state": state,
    }
    if provider == "google":
        params["prompt"] = "select_account"
    return RedirectResponse(conf["authorize_url"] + "?" + urlencode(params))


def _front(fragment: str) -> RedirectResponse:
    return RedirectResponse(f"{settings.public_base_url.rstrip('/')}/{fragment}")


async def _generate_username(db, name: str) -> str:
    base = re.sub(r"[^a-z0-9]", "", (name or "user").lower())[:20] or "user"
    if len(base) < 3:
        base = (base + "user")[:20]
    candidate = base
    for _ in range(25):
        exists = (
            await db.execute(
                select(User).where(func.lower(User.username) == candidate.lower())
            )
        ).scalar_one_or_none()
        if exists is None:
            return candidate
        candidate = f"{base}{secrets.randbelow(9000) + 1000}"[:32]
    return f"{base}{secrets.token_hex(4)}"[:32]


async def find_or_create_user(db, provider: str, norm: dict) -> User:
    # 1. Known external identity → that user.
    acct = (
        await db.execute(
            select(OAuthAccount).where(
                OAuthAccount.provider == provider,
                OAuthAccount.provider_account_id == norm["sub"],
            )
        )
    ).scalar_one_or_none()
    if acct is not None:
        return await db.get(User, acct.user_id)

    # 2. Link to an existing account ONLY on a verified email match (providers
    #    give us email_verified; never auto-link on an unverified address).
    user = None
    if norm.get("email") and norm.get("email_verified"):
        user = (
            await db.execute(
                select(User).where(func.lower(User.email) == norm["email"].lower())
            )
        ).scalar_one_or_none()

    # 3. Otherwise create a fresh account (no password). Only adopt the
    #    provider email if it's verified AND not already taken; otherwise use a
    #    synthetic address so an unverified/duplicate email can't collide or
    #    silently claim someone else's identity.
    if user is None:
        username = await _generate_username(db, norm.get("name"))
        email = norm.get("email") if norm.get("email_verified") else None
        if email:
            taken = (
                await db.execute(
                    select(User).where(func.lower(User.email) == email.lower())
                )
            ).scalar_one_or_none()
            if taken is not None:
                email = None
        if not email:
            email = f"{username}@{provider}.oauth"
        user = User(
            username=username,
            email=email,
            password_hash=None,
            display_name=sanitize_text(norm.get("name"), max_length=64) or username,
        )
        db.add(user)
        await db.flush()

    db.add(
        OAuthAccount(
            user_id=user.id, provider=provider, provider_account_id=norm["sub"]
        )
    )
    return user


@router.get("/{provider}/callback")
async def callback(
    provider: str,
    db: DB,
    code: str = "",
    state: str = "",
    error: str = "",
):
    if not is_enabled(provider):
        raise HTTPException(status_code=404, detail="Provider not enabled")
    if error or not code or not state:
        return _front("#error=oauth_cancelled")
    if await take_state(state) != provider:
        return _front("#error=bad_state")

    try:
        info = await fetch_userinfo(provider, code)
    except Exception:
        return _front("#error=oauth_exchange_failed")

    norm = normalize(provider, info)
    if not norm["sub"]:
        return _front("#error=no_identity")

    user = await find_or_create_user(db, provider, norm)
    if not user.is_active:
        return _front("#error=account_disabled")
    await db.commit()

    access = create_access_token(user.id, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    return _front(f"#access={access}&refresh={refresh}")
