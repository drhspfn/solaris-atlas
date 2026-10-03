import hashlib
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.i18n import (
    Locale,
    LocalizationContent,
    LocalizationKey,
    LocalizationValue,
)
from wuwa_story.db.models.raw import SourceFile, SourceRecord


def _localization_key_identity(raw_key: Any) -> tuple[str, dict[str, Any]]:
    """Map compiler string/integer key scalars to the string-backed DB identity."""
    if isinstance(raw_key, str):
        return raw_key, {"raw_key_type": "string", "raw_key_value": raw_key}
    if isinstance(raw_key, int) and not isinstance(raw_key, bool):
        return str(raw_key), {"raw_key_type": "integer", "raw_key_value": raw_key}
    raise ValueError(f"Unsupported localization key type: {type(raw_key).__name__}")


async def import_localization_batch(
    session: AsyncSession, release_id: int, rows: Iterable[dict[str, Any]]
) -> tuple[int, int]:
    """Idempotently import aggregated canonical localization identities and values."""
    batch = list(rows)
    if not batch:
        return 0, 0
    locales = {row.code: row.id for row in await session.scalars(select(Locale))}
    normalized_keys = [_localization_key_identity(row["key"]) for row in batch]
    keys = [
        {
            "key": normalized,
            "namespace": row.get("namespace"),
            "first_release_id": release_id,
            "metadata_json": metadata,
        }
        for row, (normalized, metadata) in zip(batch, normalized_keys, strict=True)
    ]
    key_result = await session.execute(
        insert(LocalizationKey)
        .values(keys)
        .on_conflict_do_nothing(index_elements=[LocalizationKey.key])
    )
    key_rows = await session.execute(
        select(LocalizationKey.key, LocalizationKey.id).where(
            LocalizationKey.key.in_([key for key, _ in normalized_keys])
        )
    )
    key_ids = dict(key_rows.all())
    source_refs: dict[str, set[int]] = {}
    for row in batch:
        for value in row.get("values_by_locale", {}).values():
            for source in value.get("sources") or []:
                if isinstance(source.get("file"), str) and isinstance(source.get("position"), int):
                    source_refs.setdefault(source["file"], set()).add(source["position"])
    source_record_ids: dict[tuple[str, int], int] = {}
    if source_refs:
        source_file_rows = await session.execute(
            select(SourceFile.logical_source_path, SourceFile.id).where(
                SourceFile.release_id == release_id,
                SourceFile.logical_source_path.in_(source_refs),
            )
        )
        source_file_ids = dict(source_file_rows.all())
        for source_path, positions in source_refs.items():
            source_file_id = source_file_ids.get(source_path)
            if source_file_id is None:
                continue
            ordered_positions = sorted(positions)
            for offset in range(0, len(ordered_positions), 1000):
                position_chunk = ordered_positions[offset : offset + 1000]
                source_rows = await session.execute(
                    select(SourceRecord.row_index, SourceRecord.id).where(
                        SourceRecord.release_id == release_id,
                        SourceRecord.source_file_id == source_file_id,
                        SourceRecord.row_index.in_(position_chunk),
                    )
                )
                source_record_ids.update(
                    ((source_path, position), record_id)
                    for position, record_id in source_rows
                    if position is not None
                )
    values: list[dict[str, Any]] = []
    for row, (normalized_key, _) in zip(batch, normalized_keys, strict=True):
        key_id = key_ids[normalized_key]
        for locale, value in row.get("values_by_locale", {}).items():
            if locale not in locales:
                raise ValueError(f"Locale {locale!r} has not been seeded")
            resolution = value.get("resolution", "missing_key")
            if resolution == "missing_key":
                continue
            if resolution not in ("resolved_nonempty", "resolved_empty", "broken_redirect"):
                raise ValueError(f"Unknown localization resolution: {resolution!r}")
            content = value.get("content")
            if content is not None and not isinstance(content, str):
                raise ValueError("Localization content must be a string or null")
            if resolution == "resolved_nonempty" and not content:
                raise ValueError(
                    "resolved_nonempty localization value must contain nonempty content"
                )
            if resolution == "resolved_empty" and content != "":
                raise ValueError("resolved_empty localization value must contain an empty string")
            source_record_id = None
            for source in value.get("sources") or []:
                source_record_id = source_record_ids.get(
                    (source.get("file"), source.get("position"))
                )
                if source_record_id is not None:
                    break
            content_hash = (
                hashlib.sha256(content.encode("utf-8")).digest()
                if isinstance(content, str)
                else None
            )
            values.append(
                {
                    "release_id": release_id,
                    "key_id": key_id,
                    "locale_id": locales[locale],
                    "content": content,
                    "status": resolution,
                    "redirect_key_id": None,
                    "source_record_id": source_record_id,
                    "content_hash": content_hash,
                }
            )
    value_result = None
    if values:
        # Stable ordering reduces lock-order inversions between concurrent imports.
        contents = {
            value["content_hash"]: value["content"]
            for value in values
            if value["content_hash"] is not None
        }
        content_ids: dict[bytes, int] = {}
        ordered_hashes = sorted(contents)
        for offset in range(0, len(ordered_hashes), 1000):
            hashes = ordered_hashes[offset : offset + 1000]
            await session.execute(
                insert(LocalizationContent)
                .values(
                    [{"content_hash": digest, "content": contents[digest]} for digest in hashes]
                )
                .on_conflict_do_nothing(index_elements=[LocalizationContent.content_hash])
            )
            stored = await session.execute(
                select(
                    LocalizationContent.id,
                    LocalizationContent.content_hash,
                    LocalizationContent.content,
                ).where(LocalizationContent.content_hash.in_(hashes))
            )
            for content_id, digest, content in stored:
                if content != contents[digest]:
                    raise ValueError("Localization content hash collision or corrupt dictionary")
                content_ids[digest] = content_id
        for value in values:
            digest = value.pop("content_hash")
            value.pop("content")
            value["content_id"] = content_ids[digest] if digest is not None else None
        value_result = await session.execute(
            insert(LocalizationValue)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[
                    LocalizationValue.release_id,
                    LocalizationValue.key_id,
                    LocalizationValue.locale_id,
                ]
            )
        )
    return max(getattr(key_result, "rowcount", 0) or 0, 0), max(
        getattr(value_result, "rowcount", 0) or 0, 0
    ) if value_result else 0
