from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .sanitize import sanitize_text, validate_username


# --- Auth -----------------------------------------------------------------

class RegisterIn(BaseModel):
    username: str
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    display_name: str | None = Field(default=None, max_length=64)

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


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    kind: str
    slug: str | None
    name: str
    topic: str
    created_by: str | None
    created_at: datetime
    member_count: int | None = None
    is_member: bool | None = None


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=512)


# --- Messages -------------------------------------------------------------

class MessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    reply_to_id: str | None = None


class MessageEdit(BaseModel):
    content: str = Field(min_length=1, max_length=4000)


class ReplyPreview(BaseModel):
    id: str
    sender_name: str
    content: str  # truncated snippet of the parent message


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
    reactions: list[ReactionSummary] = []
    reply_to: ReplyPreview | None = None
    mentions: list[MentionOut] = []


# --- DMs ------------------------------------------------------------------

class DMCreate(BaseModel):
    user_id: str


# --- Audit ----------------------------------------------------------------

class AuditOut(BaseModel):
    id: str
    actor: str | None  # actor username
    action: str
    target: str | None  # target username
    channel_id: str | None
    detail: str
    created_at: datetime
