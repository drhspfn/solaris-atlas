"""Small, validated cursor helpers for keyset-paginated endpoints."""
import base64
import binascii
import hashlib
import json


def cursor_scope(*parts):
    return hashlib.sha256(json.dumps(parts, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()[:20]


def encode_cursor(values, scope):
    raw = json.dumps({"v": 1, "scope": scope, "values": values},
                     separators=(",", ":"), ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(token, scope, types):
    try:
        if not isinstance(token, str) or len(token) > 2048:
            raise ValueError("Invalid cursor")
        raw = base64.b64decode(token + "=" * (-len(token) %
                               4), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Invalid cursor")
        values = data["values"]
        if (data.get("v") != 1 or data.get("scope") != scope or not isinstance(values, list)
                or len(values) != len(types) or any(type(value) is not typ for value, typ in zip(values, types, strict=True))):
            raise ValueError("Invalid cursor")
        return values
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error) as exc:
        raise ValueError("Invalid cursor") from exc


def cursor_page(rows, limit, scope, key):
    visible = rows[:limit]
    more = len(rows) > limit
    return visible, encode_cursor(key(visible[-1]), scope) if more and visible else None
