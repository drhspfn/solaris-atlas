"""Generic database-backed runtime settings registry defined in code."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.ops import AppSetting

SettingType = Literal[
    "integer",
    "number",
    "decimal",
    "boolean",
    "string",
    "enum",
    "list",
    "secret",
]

_TYPE_ALIASES: dict[str, SettingType] = {
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}


@dataclass(frozen=True, slots=True)
class SettingSpec:
    key: str
    category: str
    name: str
    value_type: SettingType
    description: str | None = None
    validation: dict[str, Any] = field(default_factory=dict)
    choices: tuple[str, ...] | None = None
    multi: bool = False
    is_secret: bool = False
    default: Any = None
    fallback: Callable[[], Any] | None = None

    def normalized_type(self) -> SettingType:
        return _TYPE_ALIASES.get(self.value_type, self.value_type)

    def resolve_default(self) -> Any:
        if self.fallback is not None:
            return self.fallback()
        return self.default


_REGISTRY: dict[str, SettingSpec] = {}


def register_setting(spec: SettingSpec) -> SettingSpec:
    _REGISTRY[spec.key] = spec
    return spec


def register_settings(specs: Sequence[SettingSpec]) -> None:
    for spec in specs:
        register_setting(spec)


def get_setting_spec(key: str) -> SettingSpec | None:
    return _REGISTRY.get(key)


def list_setting_specs() -> list[SettingSpec]:
    return list(_REGISTRY.values())


def _check_numeric_bounds(value: int | float | Decimal, validation: dict[str, Any]) -> None:
    minimum = validation.get("min")
    if minimum is not None and value < Decimal(str(minimum)) if isinstance(value, Decimal) else (minimum is not None and value < minimum):
        raise ValueError(f"Value must be >= {minimum}")
    maximum = validation.get("max")
    if maximum is not None and value > Decimal(str(maximum)) if isinstance(value, Decimal) else (maximum is not None and value > maximum):
        raise ValueError(f"Value must be <= {maximum}")


def _check_string_rules(value: str, validation: dict[str, Any]) -> None:
    min_len = validation.get("min_length")
    if min_len is not None and len(value) < int(min_len):
        raise ValueError(f"Length must be at least {min_len}")
    max_len = validation.get("max_length")
    if max_len is not None and len(value) > int(max_len):
        raise ValueError(f"Length must be at most {max_len}")
    pattern = validation.get("pattern")
    if pattern and not re.fullmatch(str(pattern), value):
        raise ValueError("Value does not match required format")
    if validation.get("timezone"):
        try:
            ZoneInfo(value)
        except Exception as exc:
            raise ValueError(f"Invalid IANA timezone: {value}") from exc


def parse_setting_value(spec: SettingSpec, raw: Any) -> tuple[Any, str | None]:
    """Validate and convert an input value into (typed_value, serialized_db_text)."""
    if raw is None:
        return None, None
    kind = spec.normalized_type()
    validation = spec.validation or {}

    if kind == "integer":
        if isinstance(raw, bool):
            raise ValueError("Expected an integer value")
        if isinstance(raw, int):
            parsed = raw
        elif isinstance(raw, str) and re.fullmatch(r"[+-]?\d+", raw.strip()):
            parsed = int(raw.strip())
        else:
            raise ValueError("Expected an integer value")
        _check_numeric_bounds(parsed, validation)
        return parsed, str(parsed)

    if kind == "number":
        if isinstance(raw, bool):
            raise ValueError("Expected a numeric value")
        try:
            parsed_num = float(raw) if isinstance(
                raw, (int, float, str)) else float("nan")
        except ValueError as exc:
            raise ValueError("Expected a numeric value") from exc
        if not math.isfinite(parsed_num):
            raise ValueError("Numeric value must be finite")
        _check_numeric_bounds(parsed_num, validation)
        return parsed_num, str(parsed_num)

    if kind == "decimal":
        if isinstance(raw, bool):
            raise ValueError("Expected a decimal value")
        try:
            parsed_dec = Decimal(str(raw).strip())
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Expected a decimal value") from exc
        if not parsed_dec.is_finite():
            raise ValueError("Decimal value must be finite")
        _check_numeric_bounds(parsed_dec, validation)
        return parsed_dec, str(parsed_dec)

    if kind == "boolean":
        if isinstance(raw, bool):
            parsed_bool = raw
        elif isinstance(raw, str):
            norm = raw.strip().lower()
            if norm in {"true", "1", "yes", "on"}:
                parsed_bool = True
            elif norm in {"false", "0", "no", "off"}:
                parsed_bool = False
            else:
                raise ValueError("Expected true or false")
        elif isinstance(raw, int) and raw in (0, 1):
            parsed_bool = bool(raw)
        else:
            raise ValueError("Expected true or false")
        return parsed_bool, ("true" if parsed_bool else "false")

    if kind == "enum":
        text_val = str(raw).strip()
        if not spec.choices or text_val not in spec.choices:
            allowed = ", ".join(spec.choices or ())
            raise ValueError(f"Expected one of: {allowed}")
        return text_val, text_val

    if kind == "list" or spec.multi:
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("["):
                try:
                    decoded = json.loads(stripped)
                except ValueError as exc:
                    raise ValueError("Invalid JSON list") from exc
                if not isinstance(decoded, list):
                    raise ValueError("Expected a list of values")
                raw_items = decoded
            elif stripped == "":
                raw_items = []
            else:
                raw_items = re.split(r"[\r\n,]+", stripped)
        elif isinstance(raw, (list, tuple, set)):
            raw_items = list(raw)
        else:
            raise ValueError("Expected a list of values")
        items: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            val = str(item).strip()
            if not val or val in seen:
                continue
            seen.add(val)
            if spec.choices and val not in spec.choices:
                raise ValueError(f"Unsupported choice: {val}")
            _check_string_rules(val, validation)
            items.append(val)
        min_items = validation.get("min_items")
        if min_items is not None and len(items) < int(min_items):
            raise ValueError(f"Select at least {min_items} item(s)")
        max_items = validation.get("max_items")
        if max_items is not None and len(items) > int(max_items):
            raise ValueError(f"Select at most {max_items} item(s)")
        return items, json.dumps(items)

    # string / secret
    if not isinstance(raw, str):
        raise ValueError("Expected a text value")
    text_val = raw if (kind == "secret" or spec.is_secret) else raw.strip()
    _check_string_rules(text_val, validation)
    return text_val, text_val


def decode_stored_value(spec: SettingSpec, stored: str | None) -> Any:
    if stored is None:
        return spec.resolve_default()
    try:
        typed, _ = parse_setting_value(spec, stored)
    except ValueError:
        return spec.resolve_default()
    return typed


async def load_app_settings_rows(
    session: Any, keys: Sequence[str] | None = None
) -> dict[str, AppSetting]:
    """Load persisted AppSetting rows without crashing on unmigrated/mock sessions."""
    if not isinstance(session, AsyncSession):
        return {}
    stmt = select(AppSetting)
    if keys:
        stmt = stmt.where(AppSetting.key.in_(list(keys)))
    try:
        if session.in_transaction():
            async with session.begin_nested():
                rows = list(await session.scalars(stmt))
        else:
            rows = list(await session.scalars(stmt))
    except (SQLAlchemyError, TypeError, AttributeError):
        return {}
    return {row.key: row for row in rows if isinstance(row, AppSetting)}


async def get_setting_values(
    session: Any, keys: Sequence[str] | None = None
) -> dict[str, Any]:
    """Return effective typed values for requested (or all registered) settings."""
    specs = (
        [spec for key in keys if (spec := _REGISTRY.get(key)) is not None]
        if keys is not None
        else list(_REGISTRY.values())
    )
    rows = await load_app_settings_rows(session, [spec.key for spec in specs])
    return {
        spec.key: decode_stored_value(
            spec, rows[spec.key].value if spec.key in rows else None)
        for spec in specs
    }


async def get_setting(session: Any, key: str, default: Any = None) -> Any:
    spec = _REGISTRY.get(key)
    rows = await load_app_settings_rows(session, [key])
    row = rows.get(key)
    if spec is None:
        return row.value if row is not None and row.value is not None else default
    return decode_stored_value(spec, row.value if row is not None else None)


async def sync_setting_definitions(session: AsyncSession) -> dict[str, AppSetting]:
    """Ensure every code-registered SettingSpec is reflected in ops.app_setting without overwriting values."""
    existing = await load_app_settings_rows(session)
    for spec in _REGISTRY.values():
        row = existing.get(spec.key)
        choices_list = list(spec.choices) if spec.choices is not None else None
        is_secret = spec.is_secret or spec.normalized_type() == "secret"
        if row is None:
            row = AppSetting(
                key=spec.key,
                category=spec.category,
                name=spec.name,
                description=spec.description,
                value_type=spec.normalized_type(),
                value=None,
                validation=dict(spec.validation),
                choices=choices_list,
                multi=spec.multi,
                is_secret=is_secret,
            )
            session.add(row)
            existing[spec.key] = row
        else:
            row.category = spec.category
            row.name = spec.name
            row.description = spec.description
            row.value_type = spec.normalized_type()
            row.validation = dict(spec.validation)
            row.choices = choices_list
            row.multi = spec.multi
            row.is_secret = is_secret
    await session.flush()
    return existing


def _mask_secret(raw: Any) -> str:
    return "••••••••" if raw else ""


def serialize_setting(spec: SettingSpec, row: AppSetting | None) -> dict[str, Any]:
    """Serialize setting metadata & effective value safely for admin UI (masking secrets)."""
    is_secret = spec.is_secret or spec.normalized_type() == "secret"
    has_override = row is not None and row.value is not None
    raw_effective = (
        decode_stored_value(spec, row.value if has_override else None)
    )
    default_val = spec.resolve_default()

    def _public_value(val: Any) -> Any:
        if val is None:
            return None
        if is_secret:
            return _mask_secret(val)
        if isinstance(val, Decimal):
            return str(val)
        return val

    return {
        "key": spec.key,
        "category": spec.category,
        "name": spec.name,
        "description": spec.description,
        "value_type": spec.normalized_type(),
        "validation": dict(spec.validation),
        "choices": list(spec.choices) if spec.choices is not None else None,
        "multi": spec.multi,
        "is_secret": is_secret,
        "source": "database" if has_override else "default",
        "value": None if (is_secret or not has_override) else _public_value(raw_effective),
        "effective_value": _public_value(raw_effective),
        "default_value": _public_value(default_val),
        "is_configured": bool(raw_effective) if is_secret else (raw_effective is not None),
        "updated_at": (
            row.updated_at.isoformat()
            if row is not None and has_override and row.updated_at is not None
            else None
        ),
    }
