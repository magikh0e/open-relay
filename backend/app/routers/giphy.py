import httpx
from fastapi import APIRouter, HTTPException, Query

from ..config import settings
from ..deps import CurrentUser

router = APIRouter(prefix="/giphy", tags=["giphy"])

GIPHY_API = "https://api.giphy.com/v1/gifs"


def _normalize(items: list[dict]) -> list[dict]:
    out = []
    for g in items:
        images = g.get("images", {})
        full = images.get("fixed_height") or images.get("downsized") or {}
        preview = images.get("fixed_height_small") or images.get("preview_gif") or full
        url = full.get("url")
        if not url:
            continue
        out.append(
            {
                "id": g.get("id"),
                "title": g.get("title") or "GIF",
                "url": url,  # media*.giphy.com/... .gif
                "preview": preview.get("url") or url,
                "width": int(full.get("width") or 0),
                "height": int(full.get("height") or 0),
            }
        )
    return out


async def _giphy(path: str, params: dict) -> list[dict]:
    if not settings.giphy_api_key:
        raise HTTPException(status_code=503, detail="Giphy is not configured")
    params = {**params, "api_key": settings.giphy_api_key}
    async with httpx.AsyncClient(timeout=8.0) as client:
        r = await client.get(f"{GIPHY_API}/{path}", params=params)
        r.raise_for_status()
        return _normalize(r.json().get("data", []))


@router.get("/enabled")
async def enabled() -> dict:
    return {"enabled": bool(settings.giphy_api_key)}


@router.get("/trending")
async def trending(user: CurrentUser, limit: int = Query(default=24, ge=1, le=50)):
    return await _giphy("trending", {"limit": limit, "rating": "pg-13"})


@router.get("/search")
async def search(
    q: str, user: CurrentUser, limit: int = Query(default=24, ge=1, le=50)
):
    q = q.strip()
    if not q:
        return []
    return await _giphy(
        "search", {"q": q, "limit": limit, "rating": "pg-13", "lang": "en"}
    )
