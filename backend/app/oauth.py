"""OAuth2 / SSO helpers: provider configs, token exchange, and CSRF state."""
import httpx

from .config import settings
from .redis_client import redis_client

_PROVIDERS = {
    "google": {
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scope": "openid email profile",
    },
    "discord": {
        "authorize_url": "https://discord.com/api/oauth2/authorize",
        "token_url": "https://discord.com/api/oauth2/token",
        "userinfo_url": "https://discord.com/api/users/@me",
        "scope": "identify email",
    },
}


def _creds(provider: str) -> tuple[str, str]:
    if provider == "google":
        return settings.google_client_id, settings.google_client_secret
    if provider == "discord":
        return settings.discord_client_id, settings.discord_client_secret
    return "", ""


def provider_conf(provider: str) -> dict | None:
    base = _PROVIDERS.get(provider)
    if base is None:
        return None
    cid, secret = _creds(provider)
    return {**base, "client_id": cid, "client_secret": secret}


def is_enabled(provider: str) -> bool:
    cid, secret = _creds(provider)
    return bool(cid and secret)


def enabled_providers() -> list[str]:
    return [p for p in _PROVIDERS if is_enabled(p)]


def redirect_uri(provider: str) -> str:
    base = settings.public_base_url.rstrip("/")
    return f"{base}/api/auth/oauth/{provider}/callback"


# --- CSRF state (short-lived, stored in Redis) ----------------------------

async def save_state(state: str, provider: str) -> None:
    await redis_client.set(f"oauth:state:{state}", provider, ex=600)


async def take_state(state: str) -> str | None:
    key = f"oauth:state:{state}"
    val = await redis_client.get(key)
    if val is not None:
        await redis_client.delete(key)
    return val


# --- token exchange + userinfo --------------------------------------------

async def fetch_userinfo(provider: str, code: str) -> dict:
    conf = provider_conf(provider)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri(provider),
        "client_id": conf["client_id"],
        "client_secret": conf["client_secret"],
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        tok = await client.post(
            conf["token_url"], data=data, headers={"Accept": "application/json"}
        )
        tok.raise_for_status()
        access = tok.json()["access_token"]
        info = await client.get(
            conf["userinfo_url"], headers={"Authorization": f"Bearer {access}"}
        )
        info.raise_for_status()
        return info.json()


def normalize(provider: str, info: dict) -> dict:
    """Map a provider's userinfo to {sub, email, email_verified, name}."""
    if provider == "google":
        return {
            "sub": str(info.get("sub") or ""),
            "email": info.get("email"),
            "email_verified": bool(info.get("email_verified")),
            "name": info.get("name") or (info.get("email") or "").split("@")[0],
        }
    if provider == "discord":
        return {
            "sub": str(info.get("id") or ""),
            "email": info.get("email"),
            "email_verified": bool(info.get("verified")),
            "name": info.get("global_name") or info.get("username") or "user",
        }
    return {"sub": "", "email": None, "email_verified": False, "name": "user"}
