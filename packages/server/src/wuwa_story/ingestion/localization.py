import hashlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from time import monotonic
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
from wuwa_story.ingestion.import_progress import import_debug_enabled

logger = logging.getLogger(__name__)

_KEY_INSERT_CHUNK = 4000
_CONTENT_CHUNK = 4000
_VALUE_INSERT_CHUNK = 3000
_SOURCE_POS_CHUNK = 5000


@dataclass
class LocalizationImportCache:
    """In-memory caches reused across batches within a release import."""

    locales: dict[str, int] | None = None
    source_file_ids: dict[str, int] | None = None
    key_ids: dict[str, int] = field(default_factory=dict)
    content_ids: dict[bytes, tuple[int, str]] = field(default_factory=dict)
    max_key_cache: int = 200_000
    max_content_cache: int = 100_000

    def reset_transactional(self) -> None:
        """Drop IDs that might have been rolled back on deadlock/retry."""
        self.key_ids.clear()
        self.content_ids.clear()

    def remember_keys(self, mapping: dict[str, int]) -> None:
        if len(self.key_ids) + len(mapping) > self.max_key_cache:
            self.key_ids.clear()
        self.key_ids.update(mapping)

    def remember_contents(self, mapping: dict[bytes, tuple[int, str]]) -> None:
        if len(self.content_ids) + len(mapping) > self.max_content_cache:
            self.content_ids.clear()
        self.content_ids.update(mapping)


def _localization_key_identity(raw_key: Any) -> tuple[str, dict[str, Any]]:
    """Map compiler string/integer key scalars to the string-backed DB identity."""
    if isinstance(raw_key, str):
        return raw_key, {"raw_key_type": "string", "raw_key_value": raw_key}
    if isinstance(raw_key, int) and not isinstance(raw_key, bool):
        return str(raw_key), {"raw_key_type": "integer", "raw_key_value": raw_key}
    raise ValueError(f"Unsupported localization key type: {type(raw_key).__name__}")


async def import_localization_batch(
    session: AsyncSession,
    release_id: int,
    rows: Iterable[dict[str, Any]],
    *,
    cache: LocalizationImportCache | None = None,
) -> tuple[int, int]:
    """Idempotently import aggregated canonical localization identities and values."""
    batch = list(rows)
    if not batch:
        return 0, 0
    debug = import_debug_enabled()
    t0 = monotonic() if debug else 0.0
    if cache is not None and cache.locales is not None:
        locales = cache.locales
    else:
        locales = {row.code: row.id for row in await session.scalars(select(Locale))}
        if cache is not None:
            cache.locales = locales
    normalized_keys = [_localization_key_identity(row["key"]) for row in batch]
    keys_by_identity: dict[str, dict[str, Any]] = {}
    for row, (normalized, metadata) in zip(batch, normalized_keys, strict=True):
        keys_by_identity.setdefault(
            normalized,
            {
                "key": normalized,
                "namespace": row.get("namespace"),
                "first_release_id": release_id,
                "metadata_json": metadata,
            },
        )
    key_ids: dict[str, int] = {}
    missing_key_names: list[str] = []
    for k in sorted(keys_by_identity):
        cached_id = cache.key_ids.get(k) if cache is not None else None
        if cached_id is not None:
            key_ids[k] = cached_id
        else:
            missing_key_names.append(k)
    inserted_key_count = 0
    if missing_key_names:
        keys_to_insert = [keys_by_identity[k] for k in missing_key_names]
        for offset in range(0, len(keys_to_insert), _KEY_INSERT_CHUNK):
            chunk = keys_to_insert[offset : offset + _KEY_INSERT_CHUNK]
            key_result = await session.execute(
                insert(LocalizationKey)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=[LocalizationKey.key])
            )
            inserted_key_count += max(getattr(key_result, "rowcount", 0) or 0, 0)
        fetched_keys: dict[str, int] = {}
        for offset in range(0, len(missing_key_names), _KEY_INSERT_CHUNK):
            name_chunk = missing_key_names[offset : offset + _KEY_INSERT_CHUNK]
            key_rows = await session.execute(
                select(LocalizationKey.key, LocalizationKey.id).where(
                    LocalizationKey.key.in_(name_chunk)
                )
            )
            fetched_keys.update(key_rows.all())
        key_ids.update(fetched_keys)
        if cache is not None:
            cache.remember_keys(fetched_keys)
    t_keys = monotonic() if debug else 0.0
    source_refs: dict[str, set[int]] = {}
    for row in batch:
        for value in row.get("values_by_locale", {}).values():
            for source in value.get("sources") or []:
                if isinstance(source.get("file"), str) and isinstance(source.get("position"), int):
                    source_refs.setdefault(source["file"], set()).add(source["position"])
    source_record_ids: dict[tuple[str, int], int] = {}
    if source_refs:
        if cache is not None and cache.source_file_ids is not None:
            source_file_ids = cache.source_file_ids
        else:
            source_file_rows = await session.execute(
                select(SourceFile.logical_source_path, SourceFile.id).where(
                    SourceFile.release_id == release_id,
                )
            )
            source_file_ids = dict(source_file_rows.all())
            if cache is not None:
                cache.source_file_ids = source_file_ids
        for source_path, positions in source_refs.items():
            source_file_id = source_file_ids.get(source_path)
            if source_file_id is None:
                continue
            ordered_positions = sorted(positions)
            for offset in range(0, len(ordered_positions), _SOURCE_POS_CHUNK):
                position_chunk = ordered_positions[offset : offset + _SOURCE_POS_CHUNK]
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
    t_sources = monotonic() if debug else 0.0
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
    inserted_value_count = 0
    if values:
        # Stable ordering reduces lock-order inversions between concurrent imports.
        contents = {
            value["content_hash"]: value["content"]
            for value in values
            if value["content_hash"] is not None
        }
        content_ids: dict[bytes, int] = {}
        missing_hashes: list[bytes] = []
        for digest in sorted(contents):
            cached = cache.content_ids.get(digest) if cache is not None else None
            if cached is not None:
                cached_id, cached_content = cached
                if cached_content != contents[digest]:
                    raise ValueError("Localization content hash collision or corrupt dictionary")
                content_ids[digest] = cached_id
            else:
                missing_hashes.append(digest)
        newly_fetched_contents: dict[bytes, tuple[int, str]] = {}
        for offset in range(0, len(missing_hashes), _CONTENT_CHUNK):
            hashes = missing_hashes[offset : offset + _CONTENT_CHUNK]
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
                newly_fetched_contents[digest] = (content_id, content)
        if cache is not None and newly_fetched_contents:
            cache.remember_contents(newly_fetched_contents)
        t_contents = monotonic() if debug else 0.0
        for value in values:
            digest = value.pop("content_hash")
            value.pop("content")
            value["content_id"] = content_ids[digest] if digest is not None else None
        values.sort(key=lambda item: (item["release_id"], item["key_id"], item["locale_id"]))
        for offset in range(0, len(values), _VALUE_INSERT_CHUNK):
            value_chunk = values[offset : offset + _VALUE_INSERT_CHUNK]
            value_result = await session.execute(
                insert(LocalizationValue)
                .values(value_chunk)
                .on_conflict_do_nothing(
                    index_elements=[
                        LocalizationValue.release_id,
                        LocalizationValue.key_id,
                        LocalizationValue.locale_id,
                    ]
                )
            )
            inserted_value_count += max(getattr(value_result, "rowcount", 0) or 0, 0)
        if debug:
            now = monotonic()
            logger.info(
                "snapshot.debug.localization_batch keys=%d values=%d missing_keys=%d missing_contents=%d "
                "keys_ms=%.1f sources_ms=%.1f contents_ms=%.1f values_ms=%.1f total_ms=%.1f",
                len(batch),
                len(values),
                len(missing_key_names),
                len(missing_hashes),
                (t_keys - t0) * 1000,
                (t_sources - t_keys) * 1000,
                (t_contents - t_sources) * 1000,
                (now - t_contents) * 1000,
                (now - t0) * 1000,
            )
    return inserted_key_count, inserted_value_count
