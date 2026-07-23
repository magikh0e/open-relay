"""Audit logging + client-IP helpers for privileged actions."""
from fastapi import Request

from .models import AuditLog


def record_audit(
    db,
    actor_id: str | None,
    action: str,
    *,
    target_user_id: str | None = None,
    channel_id: str | None = None,
    detail: str = "",
) -> None:
    """Stage an audit row on the session. The caller commits it alongside the
    action, so the log and the effect land atomically."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            target_user_id=target_user_id,
            channel_id=channel_id,
            detail=detail[:512],
        )
    )


def client_ip(request: Request) -> str:
    """Best-effort real client IP. Behind our single Caddy proxy the real
    client is the last value Caddy appends to X-Forwarded-For."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"
