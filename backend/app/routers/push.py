"""Push subscription management."""
from fastapi import APIRouter, status
from sqlalchemy import delete, select

from ..deps import DB, CurrentUser
from ..models import PushSubscription
from ..push import get_vapid_keys
from ..schemas import PushSubscribeIn

router = APIRouter(prefix="/push", tags=["push"])


@router.get("/key")
async def public_key() -> dict:
    """The VAPID public key the browser needs in order to subscribe."""
    _, public = await get_vapid_keys()
    return {"public_key": public}


@router.post("/subscribe", status_code=status.HTTP_204_NO_CONTENT)
async def subscribe(body: PushSubscribeIn, db: DB, user: CurrentUser) -> None:
    existing = (
        await db.execute(
            select(PushSubscription).where(
                PushSubscription.endpoint == body.endpoint
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        # Same browser re-subscribing, possibly as a different user.
        existing.user_id = user.id
        existing.p256dh = body.p256dh
        existing.auth = body.auth
    else:
        db.add(
            PushSubscription(
                user_id=user.id,
                endpoint=body.endpoint,
                p256dh=body.p256dh,
                auth=body.auth,
            )
        )
    await db.commit()


@router.post("/unsubscribe", status_code=status.HTTP_204_NO_CONTENT)
async def unsubscribe(body: PushSubscribeIn, db: DB, user: CurrentUser) -> None:
    await db.execute(
        delete(PushSubscription).where(
            PushSubscription.endpoint == body.endpoint,
            PushSubscription.user_id == user.id,
        )
    )
    await db.commit()
