from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import aliased

from ..audit import record_audit
from ..deps import DB, CurrentUser
from ..models import AuditLog, Ban, User
from ..schemas import AuditOut

router = APIRouter(prefix="/moderation", tags=["moderation"])


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user


AdminUser = Depends(require_admin)


@router.post("/ban", status_code=status.HTTP_204_NO_CONTENT)
async def ban_user(
    target_id: str, db: DB, admin: User = AdminUser, reason: str = ""
) -> None:
    if target_id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot ban yourself")
    target = await db.get(User, target_id)
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    existing = (
        await db.execute(select(Ban).where(Ban.user_id == target_id))
    ).scalar_one_or_none()
    if existing is None:
        db.add(Ban(user_id=target_id, reason=reason, banned_by=admin.id))
    target.is_active = False
    record_audit(db, admin.id, "site.ban", target_user_id=target_id, detail=reason)
    await db.commit()


@router.post("/unban", status_code=status.HTTP_204_NO_CONTENT)
async def unban_user(target_id: str, db: DB, admin: User = AdminUser) -> None:
    ban = (
        await db.execute(select(Ban).where(Ban.user_id == target_id))
    ).scalar_one_or_none()
    if ban is not None:
        await db.delete(ban)
    target = await db.get(User, target_id)
    if target is not None:
        target.is_active = True
    record_audit(db, admin.id, "site.unban", target_user_id=target_id)
    await db.commit()


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(
    db: DB,
    admin: User = AdminUser,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[AuditOut]:
    """Recent moderation actions, newest first. Admin-only."""
    Actor = aliased(User)
    Target = aliased(User)
    rows = (
        await db.execute(
            select(AuditLog, Actor.username, Target.username)
            .outerjoin(Actor, Actor.id == AuditLog.actor_id)
            .outerjoin(Target, Target.id == AuditLog.target_user_id)
            .order_by(AuditLog.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AuditOut(
            id=log.id,
            actor=actor,
            action=log.action,
            target=target,
            channel_id=log.channel_id,
            detail=log.detail,
            created_at=log.created_at,
        )
        for log, actor, target in rows
    ]
