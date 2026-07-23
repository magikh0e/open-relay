"""Input hardening helpers (defense-in-depth).

XSS is primarily prevented at the render layer: the React frontend escapes all
user content and never uses innerHTML. These helpers add server-side
defense-in-depth so stored data is clean regardless of who renders it later
(a future email, a non-React client, an export, etc.).
"""
import re
import unicodedata

# Usernames are validated to this at registration; mentions reuse it.
USERNAME_RE = r"[a-zA-Z0-9_.-]{3,32}"

# A mention is @username, not preceded by a word char or dot (so emails like
# "user@example.com" are not treated as a mention of "example").
MENTION_RE = re.compile(r"(?<![\w.])@(" + USERNAME_RE + r")")


def sanitize_text(value: str | None, *, max_length: int, allow_newlines: bool = False) -> str:
    """Normalize Unicode, strip control/format/zero-width characters, trim, and
    hard-cap length. Does NOT strip angle brackets or other printable content —
    correctness of XSS protection lives in the escaping render layer, and
    mangling `<3` or code snippets here would corrupt legitimate data."""
    if not value:
        return ""
    # NFC normalization collapses homoglyph/compatibility tricks to a canonical form.
    value = unicodedata.normalize("NFC", value)
    allowed = {"\n", "\t"} if allow_newlines else set()
    # Drop every Unicode "Other" category char (Cc control, Cf format incl.
    # zero-width & bidi-override, Cs surrogate, Co private-use, Cn unassigned)
    # except explicitly allowed whitespace.
    cleaned = "".join(
        ch for ch in value if ch in allowed or unicodedata.category(ch)[0] != "C"
    )
    return cleaned.strip()[:max_length]


def extract_mention_usernames(content: str) -> set[str]:
    """Return the distinct lowercased usernames referenced by @mentions."""
    return {m.lower() for m in MENTION_RE.findall(content or "")}


# --- Username hardening ----------------------------------------------------

# Must start & end alphanumeric; middle may include . _ - ; total 3-32 chars.
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,30}[a-zA-Z0-9]$")

# Names that could impersonate the system or collide with UI semantics.
RESERVED_USERNAMES = {
    "admin", "administrator", "root", "system", "support", "help", "me",
    "everyone", "here", "channel", "online", "search", "moderator", "mod",
    "null", "undefined", "anonymous", "deleted", "bot",
}


def validate_username(raw: str | None) -> str:
    """Validate & normalize a username, or raise ValueError.

    - NFKC-normalizes so confusable/fullwidth forms fold to ASCII (blocks
      homoglyph impersonation; combined with case-insensitive uniqueness).
    - Enforces a strict ASCII charset (no markup/whitespace/control chars).
    - Rejects reserved names and confusing runs of punctuation.
    """
    if raw is None:
        raise ValueError("Username is required")
    u = unicodedata.normalize("NFKC", raw).strip()
    if not _USERNAME_RE.match(u):
        raise ValueError(
            "Username must be 3-32 characters, start and end with a letter or "
            "number, and use only letters, numbers, and . _ -"
        )
    if ".." in u or "__" in u or "--" in u:
        raise ValueError("Username cannot contain repeated . _ or - characters")
    if u.lower() in RESERVED_USERNAMES:
        raise ValueError("That username is reserved")
    return u
