from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://chat:chat@localhost:5432/chat"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = "dev-only-change-me"
    jwt_algorithm: str = "HS256"
    access_token_ttl_min: int = 30
    refresh_token_ttl_days: int = 30

    cors_origins: str = "http://localhost:5173"

    msg_rate_per_10s: int = 10
    # Login attempts allowed per identifier per minute (brute-force throttle).
    login_rate_per_min: int = 10

    # --- OAuth / SSO ---
    # Public origin the browser hits (used to build redirect URIs). Set to your
    # domain in prod, e.g. https://fckord.magikh0e.pl
    public_base_url: str = "http://localhost:5173"
    google_client_id: str = ""
    google_client_secret: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""

    # Dev convenience: create tables on startup instead of running migrations.
    # Leave False in production — Alembic owns the schema there.
    auto_create_tables: bool = False

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
