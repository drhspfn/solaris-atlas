import json
from pathlib import Path

import pytest
from wuwa_story.ingestion.github_snapshots import (
    load_checkpoints,
    parse_remote_heads,
    save_checkpoint,
)

from wuwa_story_worker.cli import discover_versioned_datasets


def _snapshot(root: Path, folder: str, game_version: str) -> None:
    path = root / folder
    path.mkdir(parents=True)
    (path / "manifest.json").write_text(
        json.dumps({"game_version": game_version}), encoding="utf-8"
    )


def test_discovery_sorts_snapshots_and_accepts_multiple_hotfixes(tmp_path: Path) -> None:
    _snapshot(tmp_path, "release-3.0.4", "3.0.4")
    _snapshot(tmp_path, "release-3.0.0", "3.0.0")
    _snapshot(tmp_path, "release-3.1.0", "3.1.0")

    result = discover_versioned_datasets(tmp_path, "3.0", "3.1")

    assert [(version, path.name) for version, path in result] == [
        ("3.0.0", "release-3.0.0"),
        ("3.0.4", "release-3.0.4"),
        ("3.1.0", "release-3.1.0"),
    ]


def test_discovery_fails_if_a_requested_minor_version_is_missing(tmp_path: Path) -> None:
    _snapshot(tmp_path, "3.0.0", "3.0.0")
    _snapshot(tmp_path, "3.2.0", "3.2.0")

    with pytest.raises(FileNotFoundError, match="3.1"):
        discover_versioned_datasets(tmp_path, "3.0", "3.2")


def test_discovery_rejects_malformed_manifest(tmp_path: Path) -> None:
    path = tmp_path / "broken"
    path.mkdir()
    (path / "manifest.json").write_text("not json", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid compiled dataset manifest"):
        discover_versioned_datasets(tmp_path, "3.0", "3.0")


def test_series_crosses_major_versions_without_inventing_minor_releases(tmp_path: Path) -> None:
    versions = ["1.0.0", "1.1.0", "2.0.0", "2.1.0", "3.0.0", "3.1.0"]
    for version in reversed(versions):
        _snapshot(tmp_path, version, version)
    assert [version for version, _ in discover_versioned_datasets(tmp_path, "1.0", "3.1")] == versions


@pytest.mark.parametrize("missing", ["1.0.0", "2.0.0", "2.1.0", "3.1.0"])
def test_cross_major_series_still_rejects_gaps_and_missing_endpoints(tmp_path: Path, missing: str) -> None:
    versions = ["1.0.0", "1.1.0", "2.0.0", "2.1.0", "2.2.0", "3.0.0", "3.1.0"]
    for version in versions:
        if version != missing:
            _snapshot(tmp_path, version, version)
    with pytest.raises(FileNotFoundError, match=missing.rsplit(".", 1)[0]):
        discover_versioned_datasets(tmp_path, "1.0", "3.1")


def test_cross_major_series_rejects_missing_entire_major(tmp_path: Path) -> None:
    _snapshot(tmp_path, "1.0.0", "1.0.0")
    _snapshot(tmp_path, "3.0.0", "3.0.0")
    with pytest.raises(FileNotFoundError, match="2.0"):
        discover_versioned_datasets(tmp_path, "1.0", "3.0")


def test_remote_heads_selects_numeric_release_branches_and_sorts() -> None:
    refs = """111 refs/heads/3.6
222 refs/heads/3.0
333 refs/heads/main
444 refs/heads/3.5
"""

    result = parse_remote_heads(refs, "3.0", "3.6")

    assert [(item.branch, item.commit) for item in result] == [
        ("3.0", "222"),
        ("3.5", "444"),
        ("3.6", "111"),
    ]


def test_checkpoint_is_updated_atomically_and_preserves_other_branches(tmp_path: Path) -> None:
    checkpoint = tmp_path / "state" / "checkpoints.json"
    save_checkpoint(checkpoint, "3.0", "first")
    save_checkpoint(checkpoint, "3.1", "second")
    save_checkpoint(checkpoint, "3.0", "updated")

    assert load_checkpoints(checkpoint) == {"3.0": "updated", "3.1": "second"}
