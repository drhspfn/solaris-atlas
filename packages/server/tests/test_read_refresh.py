"""Dirty-scope fencing and migration contracts, without external services."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from wuwa_story.db.models.read_refresh import ReadProjectionDirty
from wuwa_story.read_models import refresh


def migration():
    path = Path(__file__).parents[1] / "migrations/versions/0024_browse_read_models.py"
    spec = importlib.util.spec_from_file_location("browse_refresh_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def factory(session):
    session.begin = Mock(return_value=AsyncMock())
    context = AsyncMock()
    context.__aenter__.return_value = session
    return Mock(return_value=context)


def claim(entity_id=7, generation=4):
    return refresh.RefreshClaim("character", entity_id, "current", generation, uuid4(), 2)


def sql(statement):
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_queue_model_contract():
    table = ReadProjectionDirty.__table__
    assert list(table.primary_key.columns.keys()) == ["kind", "entity_id", "snapshot_key"]
    assert set(table.columns.keys()) == {
        "kind", "entity_id", "snapshot_key", "requested_generation", "processed_generation",
        "available_at", "attempts", "last_error", "lease_until", "lease_token",
    }
    ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
    assert "processed_generation <= requested_generation" in ddl
    assert "(lease_until IS NULL) = (lease_token IS NULL)" in ddl
    assert "lease_token UUID" in ddl


def test_migration_is_reversible_and_excludes_read_tables_and_storage_from_coarse_path(monkeypatch):
    module = migration()
    operations = Mock()
    inspector = Mock()
    inspector.get_table_names.return_value = []
    inspector.has_table.return_value = False
    monkeypatch.setattr(module, "op", operations)
    monkeypatch.setattr(module.sa, "inspect", Mock(return_value=inspector))
    module.upgrade()
    tables = [call.args[0] for call in operations.create_table.call_args_list]
    assert set(tables) == {f"{kind}_read" for kind in module.KINDS} | {
        "entity_read_relation", "read_projection_dirty", "read_projection_scope",
    }
    statements = "\n".join(str(call.args[0]) for call in operations.execute.call_args_list)
    assert "requested_generation = dirty.requested_generation + 1" in statements
    assert "REFERENCING OLD TABLE AS old_rows NEW TABLE AS new_rows" in statements
    assert "IF NEW.table_name IN (" in statements
    for name in (*module.READ_TABLES, *module.STORAGE_TABLES):
        assert f"'{name}'" in statements
    assert "lease_token =" not in statements  # invalidations cannot steal an active lease
    operations.reset_mock()
    module.downgrade()
    assert {call.args[0] for call in operations.drop_table.call_args_list} == set(tables)


@pytest.mark.asyncio
async def test_claim_commits_before_return_and_uses_bounded_skip_locked():
    session = AsyncMock()
    row = SimpleNamespace(kind="character", entity_id=7, snapshot_key="current",
                          requested_generation=4, attempts=0)
    session.scalars.return_value = [row]
    from datetime import UTC, datetime
    session.scalar.return_value = datetime.now(UTC)
    create = factory(session)
    claims = await refresh.claim_refreshes(create, limit=2)
    assert len(claims) == 1 and claims[0].generation == 4
    assert claims[0].lease_token == row.lease_token
    assert row.attempts == 1
    statement = sql(session.scalars.call_args.args[0])
    assert "LIMIT 2 FOR UPDATE SKIP LOCKED" in statement
    assert "lease_until <= clock_timestamp()" in statement
    assert "wuwa-deterministic-compiler" in statement
    session.begin.return_value.__aexit__.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_publish_does_not_write_or_acknowledge():
    session = AsyncMock()
    session.scalar.side_effect = [None]
    assert not await refresh.publish_refresh(factory(session), claim(), [])
    session.execute.assert_not_awaited()
    statement = sql(session.scalar.call_args.args[0])
    assert "requested_generation = 4" in statement
    assert "lease_token =" in statement
    assert "lease_until > clock_timestamp()" in statement
    assert "FOR UPDATE" in statement


@pytest.mark.asyncio
async def test_publication_stamps_scope_generation_and_commits_all_locales(monkeypatch):
    session = AsyncMock()
    row = SimpleNamespace(processed_generation=3, lease_token=uuid4(), lease_until=object())
    scope = SimpleNamespace(kind="character", snapshot_key="current", status="pending")
    session.scalar.side_effect = [row, None, False, scope, False]
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "publish_entry", publish)
    entries = [{"entity_id": 7, "locale_id": locale, "snapshot_key": "current",
                "title": "Entry", "sort_title": "entry", "card": {}, "searchable_text": "Entry", "payload": {}}
               for locale in (1, 2)]
    assert await refresh.publish_refresh(factory(session), claim(), entries)
    assert [call.args[2]["generation"] for call in publish.call_args_list] == [4, 4]
    assert [call.args[3] for call in publish.call_args_list] == [None, []]
    assert row.processed_generation == 4
    assert row.lease_token is row.lease_until is None
    assert row.attempts == 0
    assert scope.status == "pending"


@pytest.mark.asyncio
async def test_bulk_cannot_publish_and_wrong_identity_cannot_reach_database():
    create = Mock()
    with pytest.raises(ValueError, match="Bulk scopes"):
        await refresh.publish_refresh(create, claim(entity_id=0), [])
    with pytest.raises(ValueError, match="does not match"):
        await refresh.publish_refresh(create, claim(), [
            {"entity_id": 8, "locale_id": 1, "snapshot_key": "current",
             "title": "Entry", "sort_title": "entry", "card": {}, "searchable_text": "Entry", "payload": {}},
        ])
    create.assert_not_called()


@pytest.mark.asyncio
async def test_dirty_upsert_preserves_lease_and_coalesces_ids():
    session = AsyncMock()
    await refresh.mark_dirty(session, "character", [9, 7, 9])
    statement = session.execute.call_args.args[0]
    compiled = statement.compile(dialect=postgresql.dialect())
    assert [value for key, value in compiled.params.items() if key.startswith("entity_id")] == [7, 9]
    update_sql = str(compiled).split("DO UPDATE SET", 1)[1]
    assert "requested_generation" in update_sql
    assert "lease_token" not in update_sql and "lease_until" not in update_sql
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_backoff_is_capped_and_error_payload_is_not_persisted():
    session = AsyncMock()
    await refresh.fail_claim(factory(session), claim(), RuntimeError("secret-token"))
    compiled = session.execute.call_args.args[0].compile(dialect=postgresql.dialect())
    assert "RuntimeError" in compiled.params.values()
    assert "secret-token" not in str(compiled.params)
    assert "lease_token =" in str(compiled)


def test_backfill_budget_checks_rows_and_bytes_before_enqueue():
    estimate = refresh.BackfillEstimate("dialogue", "current", 1000, 17, 17000, 3 * 1024**3, 0, 65536)
    with pytest.raises(refresh.BackfillBudgetExceeded, match="estimated bytes"):
        refresh.check_backfill_budget(estimate)
    with pytest.raises(refresh.BackfillBudgetExceeded):
        refresh.check_backfill_budget(estimate, max_rows=100, max_bytes=4 * 1024**3)
    refresh.check_backfill_budget(estimate, max_rows=17000, max_bytes=3 * 1024**3)


def test_approved_operational_budget_can_override_safe_defaults(monkeypatch):
    monkeypatch.setattr(refresh, "get_settings", lambda: SimpleNamespace(
        read_projection_max_rows=2_000_000,
        read_projection_max_bytes=20 * 1024**3,
    ))
    estimate = refresh.BackfillEstimate("dialogue", "current", 109855, 13, 1428115,
                                        16 * 1024**3, 100, 6000)
    refresh.check_backfill_budget(estimate)
    with pytest.raises(refresh.BackfillBudgetExceeded):
        refresh.check_backfill_budget(estimate, max_rows=1000)


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [
    {"entity_ids": []}, {"entity_ids": [0]}, {"entity_ids": [-1]},
    {"entity_ids": [2**63]}, {"entity_ids": [True]},
    {"snapshot_key": ""}, {"snapshot_key": " "}, {"snapshot_key": "x" * 257},
])
async def test_exact_claim_filters_are_validated_before_database_access(filters):
    create = Mock()
    with pytest.raises(ValueError):
        await refresh.claim_refreshes(create, **filters)
    create.assert_not_called()


@pytest.mark.asyncio
async def test_dirty_large_request_chunks_sorted_ids_without_extra_scope_generations():
    session = AsyncMock()
    session.scalar.return_value = None
    await refresh.mark_dirty(session, 'quest', list(range(9000, 0, -1)) + [42], '3.7', debounce_seconds=0)
    assert session.scalar.await_count == 1
    statements = [call.args[0] for call in session.execute.await_args_list]
    updates = [statement for statement in statements if getattr(statement, 'is_update', False)]
    inserts = [statement for statement in statements if getattr(statement, 'is_insert', False)]
    assert len(updates) == 1
    assert len(inserts) > 1
    values = []
    for statement in inserts:
        compiled = statement.compile(dialect=postgresql.dialect())
        assert len(compiled.params) < 32767
        values.extend(value for key, value in compiled.params.items() if key.startswith('entity_id'))
        assert 'requested_generation' in str(compiled).split('DO UPDATE SET', 1)[1]
    assert values == list(range(1, 9001))
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_publish_refresh_skips_scope_lock_while_pending_rows_remain(monkeypatch):
    session = AsyncMock()
    row = SimpleNamespace(processed_generation=3, lease_token=uuid4(), lease_until=object())
    # Calls: 1) dirty row FOR UPDATE, 2) newer projection check, 3) pending EXISTS check -> True
    session.scalar.side_effect = [row, None, True]
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "publish_entry", publish)
    entries = [{"entity_id": 7, "locale_id": 1, "snapshot_key": "current",
                "title": "Entry", "sort_title": "entry", "card": {}, "searchable_text": "Entry", "payload": {}}]
    assert await refresh.publish_refresh(factory(session), claim(), entries)
    compiled = [sql(call.args[0]) for call in session.scalar.call_args_list]
    assert not any("read_projection_scope" in item and "FOR UPDATE" in item for item in compiled)
    assert row.processed_generation == 4


@pytest.mark.asyncio
async def test_publish_refresh_tolerates_scope_lock_timeout_after_commit(monkeypatch):
    from sqlalchemy.exc import DBAPIError

    session = AsyncMock()
    row = SimpleNamespace(processed_generation=3, lease_token=uuid4(), lease_until=object())
    orig = Exception("lock timeout")
    orig.sqlstate = "55P03"
    lock_err = DBAPIError("SELECT ... FOR UPDATE", {}, orig)
    # Calls: 1) dirty row, 2) newer check, 3) pending=False, then 3 retries of _lock_scope -> 55P03
    session.scalar.side_effect = [row, None, False, lock_err, lock_err, lock_err]
    monkeypatch.setattr(refresh, "publish_entry", AsyncMock(return_value=True))
    monkeypatch.setattr(refresh, "_SCOPE_RECONCILE_RETRY_DELAYS", (0.0, 0.0))
    entries = [{"entity_id": 7, "locale_id": 1, "snapshot_key": "current",
                "title": "Entry", "sort_title": "entry", "card": {}, "searchable_text": "Entry", "payload": {}}]
    assert await refresh.publish_refresh(factory(session), claim(), entries)
    assert row.processed_generation == 4


@pytest.mark.asyncio
async def test_reconcile_ready_scopes_uses_skip_locked_and_marks_ready():
    session = AsyncMock()
    scope = SimpleNamespace(kind="character", snapshot_key="current", status="pending",
                            generation=9, published_generation=0, published_at=None, updated_at=None)
    session.execute.side_effect = [SimpleNamespace(all=lambda: [("character", "current")]), None]
    session.scalar.side_effect = [scope, True]
    assert await refresh.reconcile_ready_scopes(factory(session), kinds=["character"]) == 1
    assert scope.status == "ready"
    assert scope.published_generation == 9
    lock_query = sql(session.scalar.call_args_list[0].args[0])
    assert "FOR UPDATE SKIP LOCKED" in lock_query


@pytest.mark.asyncio
async def test_estimate_backfill_uses_hash_sample_and_falls_back_to_current(monkeypatch):
    session = AsyncMock()
    monkeypatch.setattr(refresh, "get_settings", lambda: SimpleNamespace(read_projection_locales=("en",)))
    # scalar calls: entities, locales
    session.scalar.side_effect = [15651, 1]
    # execute calls: snapshot '3.7.0' sample -> empty, fallback 'current' sample -> 200 rows @ 7500 B
    session.execute.side_effect = [
        SimpleNamespace(one=lambda: (0, None)),
        SimpleNamespace(one=lambda: (200, 7500)),
    ]
    estimate = await refresh._estimate_backfill(session, "character", "3.7.0")
    assert estimate.sampled_rows == 200
    assert estimate.estimated_row_bytes == int(7500 * 1.5)
    assert estimate.estimated_bytes == 15651 * estimate.estimated_row_bytes * 2
    first_sql = sql(session.execute.call_args_list[0].args[0])
    second_sql = sql(session.execute.call_args_list[1].args[0])
    assert "hashtext" in first_sql and "LIMIT 500" in first_sql
    assert "snapshot_key = '3.7.0'" in first_sql
    assert "snapshot_key = 'current'" in second_sql


@pytest.mark.asyncio
async def test_refresh_claim_skips_relation_builder_and_stops_locales_on_proven_deletion(monkeypatch):
    from fastapi import HTTPException

    session = AsyncMock()
    locales = [SimpleNamespace(id=1), SimpleNamespace(id=2), SimpleNamespace(id=3)]
    session.scalars.side_effect = [locales]
    monkeypatch.setattr(refresh, "_deletion_proven", AsyncMock(return_value=True))
    publish = AsyncMock(return_value=True)
    monkeypatch.setattr(refresh, "publish_refresh", publish)
    builder = AsyncMock(side_effect=HTTPException(404, "NPC data is not available for this snapshot"))
    relation_builder = AsyncMock(side_effect=AssertionError("relation_builder must not run on deleted entity"))
    assert await refresh.refresh_claim(
        factory(session),
        refresh.RefreshClaim("character", 7302777, "3.7.0", 30, uuid4(), 1),
        builder=builder,
        relation_builder=relation_builder,
    )
    assert builder.await_count == 1
    relation_builder.assert_not_awaited()
    publish.assert_awaited_once()
    assert publish.call_args.args[2] == []
    assert publish.call_args.kwargs["complete_locales"] is True
