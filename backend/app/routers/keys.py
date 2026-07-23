"""End-to-end encryption key distribution for direct messages.

The server is deliberately dumb here: it stores a user's public key and their
private key *already wrapped* by the client, and hands public keys out to other
users so they can derive a shared secret. It never sees a passphrase and cannot
unwrap anything, so it cannot read encrypted DMs.
"""
from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import DB, CurrentUser
from ..models import UserKey
from ..schemas import KeyBundleIn, KeyBundleOut, PublicKeyOut

router = APIRouter(prefix="/keys", tags=["keys"])


@router.get("/me", response_model=KeyBundleOut)
async def my_keys(db: DB, user: CurrentUser) -> KeyBundleOut:
    """Fetch your own bundle so the client can unwrap it with your passphrase."""
    row = await db.get(UserKey, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="No key set up")
    return KeyBundleOut.model_validate(row)


@router.put("/me", response_model=KeyBundleOut)
async def set_my_keys(
    body: KeyBundleIn, db: DB, user: CurrentUser
) -> KeyBundleOut:
    """Publish a key bundle (first-time setup, or re-wrap after a passphrase
    change). Replacing the keypair makes older ciphertext undecryptable, which
    the client warns about before calling this."""
    row = await db.get(UserKey, user.id)
    if row is None:
        row = UserKey(user_id=user.id)
        db.add(row)
    row.public_key = body.public_key
    row.wrapped_private_key = body.wrapped_private_key
    row.salt = body.salt
    row.iv = body.iv
    await db.commit()
    await db.refresh(row)
    return KeyBundleOut.model_validate(row)


@router.get("/{user_id}", response_model=PublicKeyOut)
async def public_key(user_id: str, db: DB, user: CurrentUser) -> PublicKeyOut:
    """Another user's public key, so you can encrypt a DM to them. Public keys
    are not secret — they're only useful for sending *to* that person."""
    row = (
        await db.execute(select(UserKey).where(UserKey.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That user hasn't set up encryption yet",
        )
    return PublicKeyOut(user_id=user_id, public_key=row.public_key)
