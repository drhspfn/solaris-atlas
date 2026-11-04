import hashlib
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql
from wuwa_story.db.models.storage import FileObject, FileReference

from wuwa_story_worker import riglyph_backfill as backfill


@asynccontextmanager
async def context(value):
    yield value


@pytest.fixture
def setup(monkeypatch):
    source = FileObject(id=20, size_bytes=6, sha256=hashlib.sha256(b"bundle").digest())
    reference = FileReference(id=5, file_id=20, reference_type="story_spine",
                              metadata_json={"game_source": {"download_id": "old"}, "playback": {"old": True}})
    reader = SimpleNamespace(get=AsyncMock(side_effect=[reference, source]),
                             scalar=AsyncMock(return_value="objects/source"), expunge=Mock())
    writer = SimpleNamespace(scalar=AsyncMock(return_value=reference),
                             commit=AsyncMock(), rollback=AsyncMock())
    factory = Mock(side_effect=lambda: context(reader if factory.call_count == 1 else writer))
    lock = SimpleNamespace(scalar=AsyncMock(return_value=True), execute=AsyncMock(), commit=AsyncMock())
    engine = SimpleNamespace(connect=lambda: context(lock))
    downloads = []

    async def get(key):
        downloads.append(key)
        yield b"bundle"

    storage = SimpleNamespace(get=get, backend="s3", bucket="public")

    artifacts = object()

    def build(bundle):
        assert bundle.read_bytes() == b"bundle"
        # The publication session has not even been constructed during rendering.
        assert factory.call_count == 1
        return artifacts

    async def publish(session, bundle, published, service, run_id, **kwargs):
        assert session is writer
        assert bundle.read_bytes() == b"bundle"
        assert published is source
        assert service.storage is storage
        assert run_id is None
        assert kwargs["artifacts"] is artifacts
        return {"riglyph_file_id": 99, "animation_preview": {"recipe": backfill.RECIPE, "status": "ready", "variants": []},
                "animation_import": {"display": "animated"}}

    publisher = AsyncMock(side_effect=publish)
    monkeypatch.setitem(sys.modules, "wuwa_story_worker.riglyph_render", SimpleNamespace(
        publish_animation=publisher, build_animation=build))
    return SimpleNamespace(source=source, reference=reference, reader=reader, writer=writer,
                           factory=factory, engine=engine, lock=lock, storage=storage,
                           downloads=downloads, publisher=publisher)


async def apply(s, **kwargs):
    return await backfill.backfill_reference(s.factory, s.storage, 5, engine=s.engine, **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", backfill.REFERENCE_TYPES)
async def test_publish_preserves_source_and_metadata_and_unlocks(setup, kind):
    s = setup
    s.reference.reference_type = kind
    assert await apply(s) == "updated"
    assert s.reference.file_id == 20
    assert s.reference.metadata_json["game_source"] == {"download_id": "old"}
    assert s.reference.metadata_json["playback"] == {"old": True}
    assert s.reference.metadata_json["riglyph_file_id"] == 99
    assert s.downloads == ["objects/source"]
    s.reader.expunge.assert_called_once_with(s.source)
    s.writer.commit.assert_awaited_once()
    assert "FOR UPDATE" in str(s.writer.scalar.call_args.args[0])
    assert "pg_advisory_unlock" in str(s.lock.execute.call_args.args[0])


@pytest.mark.asyncio
async def test_current_recipe_is_idempotent_unless_rebuilding(setup):
    s = setup
    s.reference.metadata_json["animation_preview"] = {"recipe": backfill.RECIPE, "status": "ready"}
    assert await apply(s) == "current"
    s.publisher.assert_not_called()
    assert not s.downloads
    s.factory.reset_mock()
    s.reader.get.side_effect = [s.reference, s.source]
    assert await apply(s, rebuild=True) == "updated"


@pytest.mark.asyncio
async def test_contending_worker_does_no_work(setup):
    s = setup
    s.lock.scalar.return_value = False
    assert await apply(s) == "locked"
    s.factory.assert_not_called()
    s.lock.execute.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["source", "ready", "deleted", "type"])
async def test_concurrent_source_changes_roll_back_publication(setup, change):
    s = setup
    async def publish(*args, **kwargs):
        if change == "source":
            s.reference.file_id = 21
        elif change == "ready":
            s.reference.metadata_json["animation_preview"] = {"recipe": backfill.RECIPE, "status": "ready"}
        elif change == "type":
            s.reference.reference_type = "entity_image"
        else:
            s.writer.scalar.return_value = None
        return {"riglyph_file_id": 99}
    s.publisher.side_effect = publish
    assert await apply(s) == ("current" if change == "ready" else "changed")
    s.writer.rollback.assert_awaited_once()
    s.writer.commit.assert_not_called()
    assert "riglyph_file_id" not in s.reference.metadata_json


@pytest.mark.asyncio
async def test_render_failure_does_not_update_source_and_unlocks(setup):
    s = setup
    s.publisher.side_effect = RuntimeError("render failed")
    with pytest.raises(RuntimeError, match="render failed"):
        await apply(s)
    assert s.reference.file_id == 20
    assert "animation_preview" not in s.reference.metadata_json
    s.writer.commit.assert_not_called()
    s.lock.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_failure_has_no_publication_transaction(setup, monkeypatch):
    s = setup
    renderer = sys.modules["wuwa_story_worker.riglyph_render"]
    monkeypatch.setattr(renderer, "build_animation", Mock(side_effect=ValueError("invalid archive")))
    with pytest.raises(ValueError, match="invalid archive"):
        await apply(s)
    assert s.factory.call_count == 1
    s.publisher.assert_not_called()
    s.lock.execute.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["size", "checksum", "bound", "location"])
async def test_invalid_download_never_renders(setup, failure):
    s = setup
    if failure == "size":
        s.source.size_bytes = 10
    elif failure == "checksum":
        s.source.sha256 = b"bad"
    elif failure == "location":
        s.reader.scalar.return_value = None
    with pytest.raises(ValueError):
        await apply(s, max_bytes=3 if failure == "bound" else 100)
    s.publisher.assert_not_called()
    s.lock.execute.assert_awaited_once()


def test_cli_is_dry_by_default_and_selection_is_bounded():
    args = backfill.parser().parse_args([])
    assert not args.apply and not args.rebuild and args.limit == 100
    query = backfill.candidates(limit=2, cursor=10, ref_ids=[12, 13])
    sql = str(query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    for clause in ["LIMIT 2", "file_reference.id > 10", "file_reference.id IN (12, 13)", "ORDER BY", *backfill.REFERENCE_TYPES]:
        assert clause in sql
    assert "coalesce" in sql and backfill.RECIPE in sql
    assert "coalesce" not in str(backfill.candidates(limit=2, rebuild=True))
    assert not backfill.parser().parse_args(["--dry-run"]).apply


@pytest.mark.parametrize("preview,expected", [
    (None, False),
    ({"recipe": "old", "status": "ready"}, False),
    ({"recipe": backfill.RECIPE, "status": "failed"}, False),
    ({"recipe": backfill.RECIPE, "status": "ready"}, True),
])
def test_only_ready_current_recipe_is_skipped(preview, expected):
    assert backfill.current({"animation_preview": preview}) is expected


@pytest.mark.asyncio
async def test_dry_run_does_not_construct_storage_or_publish(monkeypatch, capsys):
    from wuwa_story.db import session
    from wuwa_story.storage import s3
    db = SimpleNamespace(scalars=AsyncMock(return_value=[1, 2]))
    monkeypatch.setattr(session, "SessionFactory", lambda: context(db))
    dispose = AsyncMock()
    monkeypatch.setattr(session, "engine", SimpleNamespace(dispose=dispose))
    storage = Mock(side_effect=AssertionError("dry run touched storage"))
    monkeypatch.setattr(s3, "S3Storage", storage)
    assert await backfill.run(backfill.parser().parse_args([])) == 0
    assert '"next_cursor": 2' in capsys.readouterr().out
    storage.assert_not_called()
    dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_batch_continues_after_failure_and_reports_retry_ids(monkeypatch, capsys):
    from wuwa_story.db import session
    from wuwa_story.storage import s3
    db = SimpleNamespace(scalars=AsyncMock(return_value=[1, 2]))
    monkeypatch.setattr(session, "SessionFactory", lambda: context(db))
    monkeypatch.setattr(session, "engine", SimpleNamespace(dispose=AsyncMock()))
    monkeypatch.setattr(s3, "S3Storage", Mock())
    publish = AsyncMock(side_effect=[RuntimeError("secret remote body"), "updated"])
    monkeypatch.setattr(backfill, "backfill_reference", publish)
    monkeypatch.delenv("WUWA_DB_TRANSACTION_POOLING", raising=False)
    assert await backfill.run(backfill.parser().parse_args(["--apply"])) == 1
    assert publish.await_count == 2
    output = capsys.readouterr().out
    assert '"reference_id": 1, "status": "failed"' in output
    assert '"next_cursor": 2' in output
    assert "secret" not in output
