import os
import uuid
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse

from ..config import settings
from ..deps import DB, CurrentUser
from ..models import Upload
from ..redis_client import rate_limit_hit
from ..sanitize import sanitize_text
from ..schemas import AttachmentOut

router = APIRouter(prefix="/uploads", tags=["uploads"])

# Raster images render inline; everything else is served as a download. SVG is
# deliberately excluded from inline rendering (it can carry scripts).
IMAGE_TYPES = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}
DOC_TYPES = {
    "pdf": "application/pdf",
    "txt": "text/plain",
    "csv": "text/csv",
    "md": "text/markdown",
    "json": "application/json",
    "zip": "application/zip",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
ALLOWED = {**IMAGE_TYPES, **DOC_TYPES}


def upload_url(upload_id: str) -> str:
    return f"/api/uploads/{upload_id}"


def attachment_out(up: Upload) -> AttachmentOut:
    return AttachmentOut(
        id=up.id,
        name=up.filename,
        content_type=up.content_type,
        size=up.size,
        is_image=up.is_image,
        url=upload_url(up.id),
    )


@router.post("", response_model=AttachmentOut, status_code=status.HTTP_201_CREATED)
async def upload_file(
    db: DB, user: CurrentUser, file: UploadFile = File(...)
) -> AttachmentOut:
    if await rate_limit_hit(
        f"rl:upload:{user.id}", settings.upload_rate_per_min, 60
    ):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="You're uploading too fast — wait a minute.",
        )

    original = sanitize_text(
        os.path.basename(file.filename or "file"), max_length=200
    ) or "file"
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"File type .{ext or '?'} is not allowed",
        )

    # Read with a hard size cap so a huge upload can't exhaust memory.
    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0
    chunks: list[bytes] = []
    while chunk := await file.read(64 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"File exceeds the {settings.max_upload_mb} MB limit",
            )
        chunks.append(chunk)
    data = b"".join(chunks)
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    stored_name = f"{uuid.uuid4().hex}.{ext}"
    dest_dir = Path(settings.upload_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    (dest_dir / stored_name).write_bytes(data)

    up = Upload(
        uploader_id=user.id,
        filename=original,
        stored_name=stored_name,
        content_type=ALLOWED[ext],
        size=size,
        is_image=ext in IMAGE_TYPES,
    )
    db.add(up)
    await db.commit()
    return attachment_out(up)


@router.get("/{upload_id}")
async def serve_file(upload_id: str, db: DB):
    up = await db.get(Upload, upload_id)
    if up is None:
        raise HTTPException(status_code=404, detail="Not found")
    path = Path(settings.upload_dir) / up.stored_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(
        path,
        media_type=up.content_type,
        filename=up.filename,
        # Images render inline; anything else is forced to download.
        content_disposition_type="inline" if up.is_image else "attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )
