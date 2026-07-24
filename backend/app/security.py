from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, InvalidHashError

from .config import settings

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    # Catch the whole Argon2Error family (VerifyMismatchError for a wrong
    # password, InvalidHash for a malformed/corrupted stored hash, etc.) so a
    # bad hash yields a clean auth failure (401) instead of crashing (500).
    if not password_hash:
        return False
    try:
        return _hasher.verify(password_hash, password)
    except (Argon2Error, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# --- JWT ------------------------------------------------------------------

def _create_token(sub: str, ttl: timedelta, token_type: str, tv: int = 0) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": sub,
        "type": token_type,
        # Token generation. Any token whose "tv" is behind the user's current
        # token_version is rejected, which is how sessions get revoked.
        "tv": tv,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: str, tv: int = 0) -> str:
    return _create_token(
        user_id, timedelta(minutes=settings.access_token_ttl_min), "access", tv
    )


def create_refresh_token(user_id: str, tv: int = 0) -> str:
    return _create_token(
        user_id, timedelta(days=settings.refresh_token_ttl_days), "refresh", tv
    )


def decode_token(token: str, expected_type: str) -> str | None:
    """Return the user_id (sub) if valid and of the expected type, else None."""
    claims = decode_token_claims(token, expected_type)
    return claims[0] if claims else None


def decode_token_claims(
    token: str, expected_type: str
) -> tuple[str, int] | None:
    """Return (user_id, token_version) if valid and of the expected type.

    Callers must still compare the version against the user's current
    token_version — that check is what makes revocation work.
    """
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError:
        return None
    if payload.get("type") != expected_type:
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    return sub, int(payload.get("tv", 0))
