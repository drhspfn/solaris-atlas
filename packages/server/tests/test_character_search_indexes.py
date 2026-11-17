"""Concurrent index creation is retryable after an interrupted build."""

import importlib.util
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "migrations/versions/0037_character_search_indexes.py"


@pytest.mark.parametrize("valid", [None, False, True])
def test_concurrent_indexes_recover_invalid_builds_and_downgrade(monkeypatch, valid):
    spec = importlib.util.spec_from_file_location("character_search_indexes", SOURCE)
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
    assert sum(statement.startswith("DROP INDEX CONCURRENTLY core.") for statement in statements) == (
        len(module.INDEXES) if valid is False else 0
    )
    for name, expression in module.INDEXES.items():
        assert f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {name} ON core.character_read USING gin ({expression})" in statements
    operations.execute.reset_mock()
    module.downgrade()
    assert [call.args[0] for call in operations.execute.call_args_list] == [
        f"DROP INDEX CONCURRENTLY IF EXISTS core.{name}" for name in module.INDEXES
    ]
