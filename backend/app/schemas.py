from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .sanitize import sanitize_text, validate_username


# --- Auth -----------------------------------------------------------------

class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)
    invite_code: str | None = Field(default=None, max_length=32)  # required in invite mode

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        # Raises ValueError -> 422 with the message, handled by FastAPI.
        return validate_username(v)

    @field_validator("display_name")
    @classmethod
    def _clean_display_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return sanitize_text(v, max_length=64) or None


class LoginIn(BaseModel):
    username_or_email: str
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshIn(BaseModel):
    refresh_token: str


# --- Users ----------------------------------------------------------------

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    avatar_url: str
    is_admin: bool
    # False for accounts created purely via SSO, which have no password yet —
    # the UI asks them to set one rather than confirm an existing one.
    has_password: bool = False
    # Privacy preferences, so the client can stop emitting signals immediately
    # rather than relying on the server to drop them.
    share_typing: bool = True
    share_presence: bool = True
    allow_dms: bool = True
    discoverable: bool = True
    share_last_active: bool = True


class PrivacySettings(BaseModel):
    """Partial update — omitted fields are left unchanged."""

    model_config = ConfigDict(from_attributes=True)
    share_typing: bool | None = None
    share_presence: bool | None = None
    allow_dms: bool | None = None
    discoverable: bool | None = None
    share_last_active: bool | None = None


class PasswordChange(BaseModel):
    # Omitted only by SSO accounts that have never set a password.
    current_password: str | None = None
    new_password: str = Field(min_length=8, max_length=128)


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    avatar_url: str
    is_admin: bool = False


class MemberOut(BaseModel):
    """A channel member: public user fields plus their role in the channel."""
    id: str
    username: str
    display_name: str
    avatar_url: str
    is_admin: bool = False
    role: str = "member"


class ModerateIn(BaseModel):
    user_id: str
    reason: str = Field(default="", max_length=512)


class RoleUpdate(BaseModel):
    user_id: str
    role: str  # "mod" (operator) or "member"


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str
    bio: str
    pronouns: str
    is_admin: bool = False
    created_at: datetime
    # Invite provenance. registered_via_invite is False for accounts made on an
    # open server (or via OAuth); invited_by_username names the admin whose code
    # they redeemed (null if that admin's account was since deleted).
    registered_via_invite: bool = False
    invited_by_username: str | None = None
    # Last time seen online. Null when hidden by the target's privacy setting
    # (unless the viewer is the user themselves or an admin) or never recorded.
    last_active_at: datetime | None = None


class ProfileUpdate(BaseModel):
    # Lengths are enforced here and re-clamped by the server-side sanitizer.
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    bio: str | None = Field(default=None, max_length=500)
    pronouns: str | None = Field(default=None, max_length=40)


class MentionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    username: str
    display_name: str


# --- Channels -------------------------------------------------------------

class ChannelCreate(BaseModel):
    slug: str = Field(min_length=2, max_length=48, pattern=r"^[a-z0-9-]+$")
    name: str = Field(min_length=1, max_length=64)
    topic: str = Field(default="", max_length=512)
    is_private: bool = False
    # Optional channel key for a public channel (ignored when is_private).
    password: str | None = Field(default=None, min_length=8, max_length=128)


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    slug: str | None
    name: str
    topic: str
    created_by: str | None
    created_at: datetime
    read_only: bool = False
    # Whether a channel key is set (the hash itself is never exposed).
    has_password: bool = False
    member_count: int | None = None
    is_member: bool | None = None
    # Messages since the viewer's last_read_at, and how many of those mention
    # them (so the sidebar can show a plain badge vs an @ badge).
    unread_count: int = 0
    mention_count: int = 0


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=512)
    is_private: bool | None = None
    # Channel key management. Absent = leave unchanged; "" or null (when
    # explicitly sent) = remove; a non-empty string = set. The router checks
    # `"password" in model_fields_set` to tell "absent" from "remove", and
    # enforces the minimum length there so removal can pass an empty value.
    password: str | None = Field(default=None, max_length=128)


class ChannelJoin(BaseModel):
    # Channel key, required only when the target channel is password protected.
    password: str | None = None


# --- Messages -------------------------------------------------------------

class AttachmentOut(BaseModel):
    id: str
    name: str
    content_type: str
    size: int
    is_image: bool
    url: str
    # Client-encrypted attachment: `name`/`content_type` are placeholders and
    # the real values live in `enc_meta` ciphertext.
    encrypted: bool = False
    enc_meta: str = ""


class MessageCreate(BaseModel):
    # Content may be empty when an upload is attached. Ciphertext is base64 and
    # larger than the plaintext it replaces, hence the roomier cap.
    content: str = Field(default="", max_length=12000)
    reply_to_id: str | None = None
    thread_root_id: str | None = None  # set to reply within a thread
    upload_id: str | None = None  # attach a previously-uploaded file
    encrypted: bool = False  # content is client-encrypted; server must not touch it


class MessageEdit(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


# --- Webhooks -------------------------------------------------------------

class WebhookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)  # default display name for posts


class WebhookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    channel_id: str
    name: str
    created_at: datetime


class WebhookCreated(WebhookOut):
    url: str  # full invoke URL with secret token; shown once at creation


class WebhookMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    name: str | None = Field(default=None, max_length=64)  # override display name


# --- Invites --------------------------------------------------------------

class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    code: str
    created_at: datetime
    used_at: datetime | None = None  # non-null once an account has used it
    # Audit trail: who minted the code, and which account redeemed it. Either
    # may be null if that user's account was later deleted (FK SET NULL).
    created_by_username: str | None = None
    used_by_username: str | None = None


class ReplyPreview(BaseModel):
    id: str
    sender_name: str
    content: str  # truncated snippet, or full ciphertext when encrypted
    # Ciphertext can't be truncated server-side without breaking decryption, so
    # encrypted previews carry the whole payload and the client shortens it.
    encrypted: bool = False


class ReactionIn(BaseModel):
    emoji: str = Field(min_length=1, max_length=32)


class ReactionSummary(BaseModel):
    emoji: str
    count: int
    me: bool  # did the current user react with this emoji


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    channel_id: str
    sender_id: str | None
    content: str
    created_at: datetime
    edited_at: datetime | None
    sender: UserPublic | None = None
    author_name: str | None = None  # display name for webhook posts (sender is null)
    reactions: list[ReactionSummary] = []
    reply_to: ReplyPreview | None = None
    mentions: list[MentionOut] = []
    thread_root_id: str | None = None
    reply_count: int = 0  # thread reply count (for root messages)
    last_reply_at: datetime | None = None
    encrypted: bool = False
    attachment: AttachmentOut | None = None


# --- DMs ------------------------------------------------------------------

class DMCreate(BaseModel):
    user_id: str


class AwayIn(BaseModel):
    message: str | None = None  # empty/absent clears away status


# --- Search ---------------------------------------------------------------

class SearchResult(BaseModel):
    id: str
    channel_id: str
    channel_name: str
    channel_kind: str
    sender: UserPublic | None = None
    content: str
    created_at: datetime
    thread_root_id: str | None = None


# --- Audit ----------------------------------------------------------------

class AuditOut(BaseModel):
    id: str
    actor: str | None  # actor username
    action: str
    target: str | None  # target username
    channel_id: str | None
    detail: str
    created_at: datetime


# --- End-to-end encryption keys -------------------------------------------

class KeyBundleIn(BaseModel):
    """Uploaded once when a user enables E2EE. The private key arrives already
    wrapped by the client; the server stores it opaquely."""

    public_key: str = Field(max_length=2000)
    wrapped_private_key: str = Field(max_length=8000)
    salt: str = Field(max_length=64)
    iv: str = Field(max_length=64)


class KeyBundleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    public_key: str
    wrapped_private_key: str
    salt: str
    iv: str


class PublicKeyOut(BaseModel):
    user_id: str
    public_key: str


# --- Push notifications ---------------------------------------------------

class PushSubscribeIn(BaseModel):
    endpoint: str = Field(max_length=2000)
    p256dh: str = Field(max_length=255)
    auth: str = Field(max_length=255)


class AccountDelete(BaseModel):
    """Deleting an account is irreversible, so it re-confirms the password.
    SSO-only accounts have none, hence optional."""

    password: str | None = None
