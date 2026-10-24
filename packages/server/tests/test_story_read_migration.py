"""Retry an interrupted concurrent index build without touching a valid index."""
import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "migrations/versions/0016_story_read_indexes.py"

@pytest.mark.parametrize("valid", [None, False, True])
def test_index_migration_repairs_only_invalid_builds(monkeypatch, valid):
    spec = importlib.util.spec_from_file_location("story_read_migration", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = SimpleNamespace(
        get_context=lambda: SimpleNamespace(autocommit_block=nullcontext),
        get_bind=lambda: SimpleNamespace(scalar=Mock(return_value=valid)),
        execute=Mock(), create_index=Mock(),
    )
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    assert operations.execute.call_count == int(valid is False)
    if valid is False:
        assert operations.execute.call_args.args == (
            "DROP INDEX CONCURRENTLY i18n.ix_localization_value_key_locale_release",)
    operations.create_index.assert_called_once_with(
        "ix_localization_value_key_locale_release", "localization_value",
        ["key_id", "locale_id", "release_id"], schema="i18n",
        postgresql_concurrently=True, if_not_exists=True,
    )
