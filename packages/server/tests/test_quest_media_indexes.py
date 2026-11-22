"""Concurrent media indexes can be retried after an interrupted build."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize("valid", [None, False, True])
def test_concurrent_media_indexes_recover_and_rollback(monkeypatch, valid):
    source = Path(__file__).resolve().parents[1] / "migrations/versions/0043_quest_media_indexes.py"
    spec = importlib.util.spec_from_file_location("quest_media_indexes", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    operations = SimpleNamespace(
        get_context=lambda: SimpleNamespace(autocommit_block=nullcontext),
        get_bind=lambda: SimpleNamespace(scalar=Mock(return_value=valid)),
        execute=Mock(),
    )
    monkeypatch.setattr(module, "op", operations)
    module.upgrade()
    statements = [call.args[0] for call in operations.execute.call_args_list]
    assert sum(s.startswith("DROP INDEX CONCURRENTLY storage.") for s in statements) == (
        len(module.INDEXES) if valid is False else 0
    )
    for name, columns in module.INDEXES.items():
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON storage.file_reference {columns}" in statements
    operations.execute.reset_mock()
    module.downgrade()
    assert [call.args[0] for call in operations.execute.call_args_list] == [
        f"DROP INDEX CONCURRENTLY IF EXISTS storage.{name}" for name in module.INDEXES
    ]
