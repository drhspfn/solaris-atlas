from enum import IntEnum

RESERVED_NICKNAMES = frozenset(
    {
        "admin",
        "administrator",
        "root",
        "api",
        "auth",
        "login",
        "logout",
        "register",
        "settings",
        "account",
        "accounts",
        "user",
        "users",
        "system",
        "support",
        "solaris",
        "solarisatlas",
        "solarisation",
        "null",
        "undefined",
    }
)


class UserRole(IntEnum):
    USER = 1
    ADMIN = 2


class UserStatus(IntEnum):
    ACTIVE = 1
    DISABLED = 2


class IdentityProvider(IntEnum):
    GOOGLE = 1
