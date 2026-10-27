import runpy
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.pool import NullPool

SOURCE = Path(__file__).resolve().parents[1] / "src/wuwa_story/db/session.py"


def test_transaction_pooling_uses_nullpool_and_unique_prepared_statement_names(monkeypatch):
    monkeypatch.setenv("WUWA_DB_TRANSACTION_POOLING", "1")
    with patch("sqlalchemy.ext.asyncio.create_async_engine") as create, \
         patch("sqlalchemy.event.listens_for", return_value=lambda function: function):
        runpy.run_path(str(SOURCE))
    options = create.call_args.kwargs
    assert options["poolclass"] is NullPool
    assert "pool_size" not in options
    name = options["connect_args"]["prepared_statement_name_func"]
    assert len({name() for _ in range(100)}) == 100


def test_api_transaction_limits_are_local_and_not_installed_for_worker(monkeypatch):
    from unittest.mock import MagicMock
    monkeypatch.setenv("WUWA_DB_TRANSACTION_POOLING", "1")
    with patch("sqlalchemy.ext.asyncio.create_async_engine"), \
         patch("sqlalchemy.event.listens_for", return_value=lambda f: f):
        module = runpy.run_path(str(SOURCE))
    connection = MagicMock()
    module["bound_api_transaction"](connection)
    statements = [call.args[0] for call in connection.exec_driver_sql.call_args_list]
    assert statements == ["SET LOCAL statement_timeout = '20000ms'", "SET LOCAL lock_timeout = '3s'"]


def test_direct_worker_pool_configuration_is_preserved(monkeypatch):
    monkeypatch.delenv("WUWA_DB_TRANSACTION_POOLING", raising=False)
    monkeypatch.setenv("WUWA_WORKER_DB_POOL_SIZE", "4")
    monkeypatch.setenv("WUWA_WORKER_DB_MAX_OVERFLOW", "4")
    with patch("sqlalchemy.ext.asyncio.create_async_engine") as create:
        runpy.run_path(str(SOURCE))
    assert create.call_args.kwargs == {"pool_pre_ping": True, "pool_size": 4, "max_overflow": 4}
