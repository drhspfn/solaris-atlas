import re

from email_validator import EmailNotValidError, validate_email

from wuwa_story.auth.constants import RESERVED_NICKNAMES

NICKNAME_PATTERN = re.compile(r"^[A-Za-z0-9._]{3,32}$")
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class ValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def normalize_email(email: str) -> str:
    value = email.strip()
    try:
        validate_email(value, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValidationError("INVALID_EMAIL", "Enter a valid email address.") from exc
    return value.casefold()


def validate_nickname(nickname: str) -> str:
    value = nickname
    normalized = value.casefold()
    if not NICKNAME_PATTERN.fullmatch(value):
        raise ValidationError(
            "INVALID_NICKNAME",
            "Nickname must be 3–32 characters using English letters, numbers, dots or underscores.",
        )
    if normalized in RESERVED_NICKNAMES:
        raise ValidationError("RESERVED_NICKNAME", "This nickname is reserved.")
    return normalized


def validate_password(password: str) -> None:
    length = len(password)
    if length < PASSWORD_MIN_LENGTH or length > PASSWORD_MAX_LENGTH:
        raise ValidationError("WEAK_PASSWORD", "Password must be between 8 and 128 characters.")


def suggested_nickname(email: str, taken: set[str] | None = None) -> str:
    prefix = email.partition("@")[0]
    base = re.sub(r"[^A-Za-z0-9._]", "", prefix)[:32]
    if len(base) < 3 or base.casefold() in RESERVED_NICKNAMES:
        base = "resonator"
    taken_values = taken or set()
    candidate = base
    suffix = 1
    while candidate.casefold() in taken_values or candidate.casefold() in RESERVED_NICKNAMES:
        tail = str(suffix)
        candidate = f"{base[: 32 - len(tail)]}{tail}"
        suffix += 1
    return candidate
