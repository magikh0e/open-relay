import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Channel kinds
KIND_PUBLIC = "public"
KIND_PRIVATE = "private"
KIND_DM = "dm"
KIND_GROUP = "group"  # a private multi-person DM (channel with a member set)

# Member roles
ROLE_OWNER = "owner"
ROLE_MOD = "mod"
ROLE_MEMBER = "member"


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Nullable: users who signed up via OAuth/SSO have no password.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    display_name: Mapped[str] = mapped_column(String(64))
    avatar_url: Mapped[str] = mapped_column(String(512), default="")
    bio: Mapped[str] = mapped_column(String(500), default="")
    pronouns: Mapped[str] = mapped_column(String(40), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # site-wide moderator
    # Embedded in every issued JWT. Bumping it invalidates all outstanding
    # access AND refresh tokens for this user (used on password change).
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )

    # --- privacy preferences (all opt-out; enforced server-side) ---
    # Emit "X is typing…" to others.
    share_typing: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Appear online. When false the user is never added to the presence set, so
    # they read as offline to everyone.
    share_presence: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Let other people start a new DM with you (existing DMs keep working).
    allow_dms: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Appear in user search. Does not hide you from channels you're a member of.
    discoverable: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    # Show "last active" on your profile to others. When false, only you and
    # admins see it; the timestamp itself is still recorded (see last_active_at).
    share_last_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # Last time the user was seen on a live socket. Updated on WS connect and
    # disconnect regardless of any privacy toggle; visibility is gated at read.
    last_active_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    memberships: Mapped[list["ChannelMember"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserKey(Base):
    """End-to-end encryption key material for a user's direct messages.

    The server only ever holds the PUBLIC key plus the user's private key in
    *already-encrypted* form: the client wraps it with a key derived from a
    passphrase (PBKDF2) that never leaves the browser. The server cannot unwrap
    it, so it cannot read encrypted DMs.
    """

    __tablename__ = "user_keys"

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Base64 SPKI of the ECDH P-256 public key.
    public_key: Mapped[str] = mapped_column(Text)
    # Base64 of the AES-GCM-wrapped PKCS8 private key (opaque to the server).
    wrapped_private_key: Mapped[str] = mapped_column(Text)
    # Base64 PBKDF2 salt and AES-GCM IV used to wrap the private key.
    salt: Mapped[str] = mapped_column(String(64))
    iv: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    kind: Mapped[str] = mapped_column(String(16), default=KIND_PUBLIC, index=True)
    # slug is the human-facing @name for public/private channels; NULL for DMs.
    slug: Mapped[str | None] = mapped_column(String(48), unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    topic: Mapped[str] = mapped_column(String(512), default="")
    # For DMs: a canonical sorted "userA:userB" key enforcing one DM per pair.
    dm_key: Mapped[str | None] = mapped_column(String(80), unique=True, nullable=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Read-only (announcement) channels: only site admins may post; everyone
    # else can read and react but not send. Used by the seeded #whatsnew channel.
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Optional channel key (argon2 hash), IRC "+k" style: a public channel that
    # anyone can browse but only join with the password. NULL = no password.
    # Only meaningful for public channels; private channels are invite-gated.
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    members: Mapped[list["ChannelMember"]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class ChannelMember(Base):
    __tablename__ = "channel_members"
    __table_args__ = (
        UniqueConstraint("channel_id", "user_id", name="uq_channel_user"),
        Index("ix_member_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(16), default=ROLE_MEMBER)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    # One-sided "close this DM": hides it from your sidebar without leaving the
    # channel or touching history. Cleared automatically when a new message
    # arrives, so closing is dismissal rather than a block.
    hidden: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )

    channel: Mapped[Channel] = relationship(back_populates="members")
    user: Mapped[User] = relationship(back_populates="memberships")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        # Fast "latest N in a channel" and pagination-before-cursor queries.
        Index("ix_message_channel_created", "channel_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    sender_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    # Optional inline reply to another message in the same channel. SET NULL so
    # deleting the parent leaves the reply intact (shown as "deleted message").
    reply_to_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    # Thread grouping: NULL for top-level messages; for a thread reply, the id
    # of the root message it hangs under (flattened — one level deep).
    thread_root_id: Mapped[str | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Optional file attachment.
    upload_id: Mapped[str | None] = mapped_column(
        ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True
    )
    content: Mapped[str] = mapped_column(Text)
    # True when `content` holds client-encrypted ciphertext (base64 iv+payload)
    # rather than readable text. The server never decrypts these.
    encrypted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Display name for messages posted by a webhook (sender_id is NULL then).
    author_name: Mapped[str | None] = mapped_column(String(64), nullable=True)

    sender: Mapped[User | None] = relationship()


class Upload(Base):
    """A file uploaded by a user and (usually) attached to a message."""

    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    uploader_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    filename: Mapped[str] = mapped_column(String(255))  # original (sanitized)
    stored_name: Mapped[str] = mapped_column(String(255))  # uuid.ext on disk
    content_type: Mapped[str] = mapped_column(String(128))
    size: Mapped[int] = mapped_column(Integer)
    is_image: Mapped[bool] = mapped_column(Boolean, default=False)
    # True when the stored bytes are client-encrypted. The server then knows
    # nothing about the real file: `enc_meta` holds the ciphertext of
    # {name, type}, which only the conversation's participants can read.
    encrypted: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    enc_meta: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MessageReaction(Base):
    __tablename__ = "message_reactions"
    __table_args__ = (
        # One reaction per (message, user, emoji); toggling removes it again.
        UniqueConstraint(
            "message_id", "user_id", "emoji", name="uq_message_user_emoji"
        ),
        Index("ix_reaction_message", "message_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    emoji: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class MessageMention(Base):
    __tablename__ = "message_mentions"
    __table_args__ = (
        UniqueConstraint("message_id", "user_id", name="uq_message_mention"),
        Index("ix_mention_user", "user_id"),
        Index("ix_mention_message", "message_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    message_id: Mapped[str] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ChannelBan(Base):
    """Per-channel ban: prevents a user from (re)joining a specific channel."""

    __tablename__ = "channel_bans"
    __table_args__ = (
        UniqueConstraint("channel_id", "user_id", name="uq_channel_ban"),
        Index("ix_channel_ban_channel", "channel_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE")
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    banned_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class OAuthAccount(Base):
    """Links a user to an external identity provider (Google, Discord, ...)."""

    __tablename__ = "oauth_accounts"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_account_id", name="uq_oauth_provider_account"
        ),
        Index("ix_oauth_user", "user_id"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(32))  # "google" | "discord"
    provider_account_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AuditLog(Base):
    """Immutable record of privileged/moderation actions for accountability."""

    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    actor_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(48))  # e.g. "channel.ban"
    target_user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Ban(Base):
    """Site-wide ban for moderation of a public product."""

    __tablename__ = "bans"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True
    )
    reason: Mapped[str] = mapped_column(String(512), default="")
    banned_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class AppSecret(Base):
    """Small key/value store for server-generated secrets.

    Used for the VAPID keypair: generating and persisting it here means push
    works on first boot without anyone hand-editing environment variables, and
    it stays stable across restarts (a changed key silently invalidates every
    existing push subscription).
    """

    __tablename__ = "app_secrets"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PushSubscription(Base):
    """A browser push endpoint belonging to a user.

    One row per device/browser. Endpoints go stale constantly (reinstalls,
    cleared data), so 404/410 responses from the push service delete the row.
    """

    __tablename__ = "push_subscriptions"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Webhook(Base):
    """An incoming webhook. An external system POSTs to a secret URL to post a
    message into a channel. Created by a channel owner/mod (or a site admin);
    posts appear with a null sender and the webhook's display name.
    """

    __tablename__ = "webhooks"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )


class Invite(Base):
    """A single-use signup code. Enforced only when REGISTRATION_MODE=invite;
    created by a site admin, consumed by the account that registers with it.
    """

    __tablename__ = "invites"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid
    )
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    created_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    used_by: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
