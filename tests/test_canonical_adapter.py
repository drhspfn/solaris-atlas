import json
from decimal import Decimal
from pathlib import Path

import pytest

from wuwa_story.ingestion.canonical import CanonicalRecordAdapter, detect_compiled_dataset
from wuwa_story.ingestion.raw import _iter_records, canonical_record_hash, verify_indexed_file


def test_compiled_dataset_requires_versioned_manifest_and_raw_index(tmp_path: Path) -> None:
    (tmp_path / "raw-evidence").mkdir()
    (tmp_path / "raw-evidence/index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "game_version": "3.6.0",
                "source_repository": "local",
                "schema_inventory_hash": "abc",
            }
        ),
        encoding="utf-8",
    )

    assert detect_compiled_dataset(tmp_path)
    assert CanonicalRecordAdapter(tmp_path).root == tmp_path


def test_edge_adapter_rejects_missing_provenance(tmp_path: Path) -> None:
    (tmp_path / "raw-evidence").mkdir()
    (tmp_path / "raw-evidence/index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {"game_version": "3.6.0", "source_repository": "local", "schema_inventory_hash": "abc"}
        ),
        encoding="utf-8",
    )
    (tmp_path / "graphs").mkdir()
    (tmp_path / "graphs/global.jsonl").write_text('{"from":"a"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="required provenance"):
        list(CanonicalRecordAdapter(tmp_path).iter_edges())


def test_raw_snapshot_hash_and_size_are_verified(tmp_path: Path) -> None:
    raw_file = tmp_path / "source.json"
    raw_file.write_bytes(b'{"records": []}')
    verify_indexed_file(
        raw_file,
        {
            "file": "source.json",
            "sha256": "4112b9a18df6832f78b3cd60888edc481078e0c103e6e091738f525ed4a7ce13",
            "bytes": 15,
        },
    )
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_indexed_file(raw_file, {"file": "source.json", "sha256": "wrong"})


def test_streamed_decimal_values_remain_json_numbers(tmp_path: Path) -> None:
    raw_file = tmp_path / "decimal.json"
    raw_file.write_text('[{"value": 1.25}]', encoding="utf-8")

    _, _, record = next(_iter_records(raw_file, "array"))

    assert record == {"value": 1.25}
    assert canonical_record_hash({"value": Decimal("1.25")}) == canonical_record_hash(record)


def test_unpaired_surrogate_is_represented_as_reversible_escape(tmp_path: Path) -> None:
    raw_file = tmp_path / "surrogate.json"
    raw_file.write_bytes(b'[{"value":"\\ud800"}]')

    preserve_surrogates = verify_indexed_file(raw_file, {})
    _, _, record = next(_iter_records(raw_file, "array", preserve_surrogates=preserve_surrogates))

    assert record == {"value": r"\ud800"}
    assert all(not 0xD800 <= ord(char) <= 0xDFFF for char in record["value"])


def test_jsonb_null_is_represented_as_reversible_escape(tmp_path: Path) -> None:
    raw_file = tmp_path / "jsonb-null.json"
    raw_file.write_bytes(b'[{"value":"\\u0000"}]')

    preserve_special = verify_indexed_file(raw_file, {})
    _, _, record = next(_iter_records(raw_file, "array", preserve_surrogates=preserve_special))

    assert record == {"value": r"\u0000"}


def test_git_lfs_pointer_is_preserved_as_source_record(tmp_path: Path) -> None:
    raw_file = tmp_path / "pointer.json"
    pointer = (
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:abc123\nsize 123\n"
    )
    raw_file.write_text(pointer, encoding="utf-8")

    _, _, record = next(_iter_records(raw_file, "array"))

    assert record == {"_source_representation": "git_lfs_pointer", "raw_pointer": pointer}
