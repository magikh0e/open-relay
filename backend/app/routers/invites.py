"""Admin invite codes.

Enforced at registration only when REGISTRATION_MODE=invite (see auth.register).
These endpoints let a site admin mint, list, and revoke single-use codes.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..deps import CurrentUser, DB
from ..models import Invite, User
from ..schemas import InviteOut
from .moderation import require_admin

router = APIRouter(
    prefix="/invites", tags=["invites"], dependencies=[Depends(require_admin)]
)


@router.post("", response_model=InviteOut, status_code=201)
async def create_invite(db: DB, user: CurrentUser):
    inv = Invite(code=secrets.token_urlsafe(9), created_by=user.id)
    db.add(inv)
    await db.flush()
    out = InviteOut(
        id=inv.id,
        code=inv.code,
        created_at=inv.created_at,
        used_at=None,
        created_by_username=user.username,
        used_by_username=None,
    )
    await db.commit()
    return out


@router.get("", response_model=list[InviteOut])
async def list_invites(db: DB):
    # Resolve both FKs to usernames for the audit view. Outer joins so a code
    # whose creator or redeemer was since deleted (FK SET NULL) still lists.
    creator = aliased(User)
    redeemer = aliased(User)
    rows = (
        await db.execute(
            select(Invite, creator.username, redeemer.username)
            .outerjoin(creator, Invite.created_by == creator.id)
            .outerjoin(redeemer, Invite.used_by == redeemer.id)
            .order_by(Invite.created_at.desc())
        )
    ).all()
    return [
        InviteOut(
            id=inv.id,
            code=inv.code,
            created_at=inv.created_at,
            used_at=inv.used_at,
            created_by_username=created_by_username,
            used_by_username=used_by_username,
        )
        for inv, created_by_username, used_by_username in rows
    ]


@router.delete("/{invite_id}", status_code=204)
async def delete_invite(invite_id: str, db: DB):
    inv = await db.get(Invite, invite_id)
    if inv is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    if inv.used_at is not None:
        raise HTTPException(status_code=409, detail="That invite has already been used")
    await db.delete(inv)
    await db.commit()
