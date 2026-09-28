import hashlib
import hmac
import secrets

from pwdlib import PasswordHash

PASSWORD_HASHER = PasswordHash.recommended()


def new_secret() -> str:
    return secrets.token_urlsafe(48)


def token_digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def hash_password(password: str) -> str:
    return PASSWORD_HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORD_HASHER.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def verify_password_and_update(password: str, password_hash: str) -> tuple[bool, str | None]:
    try:
        return PASSWORD_HASHER.verify_and_update(password, password_hash)
    except (ValueError, TypeError):
        return False, None


def csrf_token(secret: str) -> str:
    nonce = new_secret()
    signature = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return f"{nonce}.{signature}"


def validate_csrf_token(token: str, secret: str) -> bool:
    try:
        nonce, signature = token.rsplit(".", 1)
    except ValueError:
        return False
    expected = hmac.new(secret.encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return bool(nonce) and hmac.compare_digest(signature, expected)
