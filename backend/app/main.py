from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .database import Base, engine
from .seed import ensure_whatsnew
from .routers import (
    auth,
    channels,
    dms,
    giphy,
    messages,
    moderation,
    oauth,
    search,
    uploads,
    users,
    ws,
)
from .ws_manager import manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # In production the schema is managed by Alembic (`alembic upgrade head`,
    # run by the container entrypoint). Set AUTO_CREATE_TABLES=1 only for quick
    # local dev without migrations.
    if settings.auto_create_tables:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    await ensure_whatsnew()
    await manager.start()
    yield
    await manager.stop()
    await engine.dispose()


APP_VERSION = "1.6.2"

app = FastAPI(title="Relay API", version=APP_VERSION, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(oauth.router)
app.include_router(users.router)
app.include_router(channels.router)
app.include_router(messages.router)
app.include_router(dms.router)
app.include_router(uploads.router)
app.include_router(giphy.router)
app.include_router(search.router)
app.include_router(moderation.router)
app.include_router(ws.router)


@app.get("/health", tags=["meta"])
async def health() -> dict:
    return {"status": "ok", "app": "Relay", "version": APP_VERSION}
