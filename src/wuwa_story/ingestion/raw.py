import hashlib
import json
import re
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

import ijson
from ijson.backends import python as ijson_python
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from wuwa_story.db.models.raw import SourceFile, SourceRecord


def canonical_record_hash(record: Any) -> bytes:
    record = _json_safe(record)
    encoded = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).digest()


def _json_safe(value: Any) -> Any:
    """Convert streaming values to JSONB-safe values without dropping raw evidence."""
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return int(value)
        return float(value)
    if isinstance(value, str) and any(
        ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        return "".join(
            f"\\u{ord(char):04x}"
            if ord(char) == 0 or 0xD800 <= ord(char) <= 0xDFFF
            else char
            for char in value
        )
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def verify_indexed_file(path: Path, entry: dict[str, Any]) -> bool:
    """Fail closed if a raw evidence file differs from its immutable index entry."""
    digest = hashlib.sha256()
    size = 0
    has_jsonb_incompatible_escape = False
    overlap = b""
    incompatible_pattern = re.compile(rb"\\u(?:0000|[dD][89aAbB][0-9a-fA-F]{2})")
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            block = overlap + chunk
            has_jsonb_incompatible_escape |= bool(incompatible_pattern.search(block))
            overlap = block[-16:]
    expected_digest = entry.get("sha256")
    if expected_digest and digest.hexdigest() != expected_digest:
        raise ValueError(f"Raw source evidence hash mismatch: {entry.get('file', path.name)}")
    expected_size = entry.get("bytes")
    if expected_size is not None and size != expected_size:
        raise ValueError(f"Raw source evidence size mismatch: {entry.get('file', path.name)}")
    return has_jsonb_incompatible_escape


def _iter_records(
    path: Path, shape: str, *, preserve_surrogates: bool = False
) -> Iterator[tuple[int, str | None, Any]]:
    with path.open("rb") as stream:
        header = stream.read(64)
    if header.startswith(b"version https://git-lfs.github.com/spec/v1"):
        # Some upstream checkouts contain Git LFS pointer files instead of payloads.
        # Preserve the exact pointer as an explicit source record rather than dropping it.
        yield 0, None, {
            "_source_representation": "git_lfs_pointer",
            "raw_pointer": path.read_text(encoding="utf-8"),
        }
        return
    with path.open("rb") as stream:
        if shape == "array":
            parser = ijson_python.items if preserve_surrogates else ijson.items
            for index, record in enumerate(parser(stream, "item")):
                yield index, None, _json_safe(record)
        elif shape == "object":
            parser = ijson_python.kvitems if preserve_surrogates else ijson.kvitems
            for index, (key, record) in enumerate(parser(stream, "")):
                yield index, key, _json_safe(record)
        else:
            value = json.loads(path.read_text(encoding="utf-8"))
            yield 0, None, value


async def import_raw_snapshot(
    source_root: Path, session: AsyncSession, release_id: int, batch_size: int = 1000
) -> tuple[int, int]:
    """Stream the compiler's immutable raw-evidence tables into release-scoped rows."""
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    inventory = json.loads((source_root / "coverage.json").read_text(encoding="utf-8"))
    tables = {row["file"]: row for row in inventory.get("tables", [])}
    index = json.loads((source_root / "raw-evidence" / "index.json").read_text(encoding="utf-8"))
    seen = created = 0
    existing_counts = dict(
        (
            await session.execute(
                select(SourceRecord.source_file_id, func.count(SourceRecord.id))
                .where(SourceRecord.release_id == release_id)
                .group_by(SourceRecord.source_file_id)
            )
        ).all()
    )
    for entry in index:
        logical_path = entry.get("file")
        if (
            not logical_path
            or Path(logical_path).name == ".DS_Store"
            or Path(logical_path).name.startswith("._")
        ):
            continue
        path = source_root / "raw-evidence" / logical_path
        if not path.is_file():
            raise FileNotFoundError(f"Raw source evidence is missing: {path}")
        table = tables.get(logical_path, {})
        schema_shape = table.get("shape", "array")
        schema_bytes = json.dumps(
            [table.get("field_paths", []), table.get("all_field_names", [])],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        schema_hash = hashlib.sha256(schema_bytes).digest()
        file_record = await session.scalar(
            select(SourceFile).where(
                SourceFile.release_id == release_id,
                SourceFile.logical_source_path == logical_path,
            )
        )
        if file_record is None:
            file_record = SourceFile(
                release_id=release_id,
                logical_source_path=logical_path,
                table_name=Path(logical_path).stem,
                schema_hash=schema_hash,
                row_count=table.get("record_count"),
                metadata_json={
                    "source_sha256": entry.get("sha256"),
                    "source_status": entry.get("status"),
                    "source_bytes": entry.get("bytes"),
                },
            )
            session.add(file_record)
            await session.flush()
        elif file_record.metadata_json.get("source_sha256") != entry.get("sha256"):
            raise ValueError(
                f"Immutable source file changed within release identity: {logical_path}"
            )
        source_file_id = file_record.id
        expected_count = table.get("record_count")
        stored_count = existing_counts.get(source_file_id, 0)
        if isinstance(expected_count, int) and stored_count >= expected_count:
            # The immutable source hash and stored count prove this file was fully
            # ingested already; don't reread or stream millions of duplicate rows.
            seen += expected_count
            continue
        preserve_surrogates = verify_indexed_file(path, entry)
        batch: list[dict[str, Any]] = []
        for row_index, source_key, record in _iter_records(
            path, schema_shape, preserve_surrogates=preserve_surrogates
        ):
            seen += 1
            batch.append(
                {
                    "release_id": release_id,
                    "source_file_id": source_file_id,
                    "row_index": row_index,
                    "source_key": source_key,
                    "content_hash": canonical_record_hash(record),
                    "data": record,
                }
            )
            if len(batch) >= batch_size:
                result = await session.execute(
                    insert(SourceRecord)
                    .values(batch)
                    .on_conflict_do_nothing(
                        index_elements=[
                            SourceRecord.release_id,
                            SourceRecord.source_file_id,
                            SourceRecord.row_index,
                        ]
                    )
                )
                created += max(getattr(result, "rowcount", 0) or 0, 0)
                existing_counts[source_file_id] = existing_counts.get(source_file_id, 0) + len(batch)
                await session.commit()
                batch.clear()
        if batch:
            result = await session.execute(
                insert(SourceRecord)
                .values(batch)
                .on_conflict_do_nothing(
                    index_elements=[
                        SourceRecord.release_id,
                        SourceRecord.source_file_id,
                        SourceRecord.row_index,
                    ]
                )
            )
            created += max(getattr(result, "rowcount", 0) or 0, 0)
            existing_counts[source_file_id] = existing_counts.get(source_file_id, 0) + len(batch)
        await session.commit()
    return seen, created
