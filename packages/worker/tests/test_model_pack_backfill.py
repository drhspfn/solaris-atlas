import hashlib
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from wuwa_story.ingestion.character_models import CharacterModelJob

from wuwa_story_worker import character_models
from wuwa_story_worker import model_pack_backfill as backfill


def job():
    return CharacterModelJob(run_id=42, character_key="character:1", owner_node_id=9,
                             release_id=3, game_version="3.3.0", asset_version="3.7.0",
                             download_id="a" * 64, tier="hd", include_animations=True)


def test_source_requires_completed_matching_model_run():
    run = NS(id=42, status="completed", target_node_id=9, metadata_json={"payload": job().model_dump()})
    assert backfill.source_job(run, "character_model") == job()
    for status in ("running", "failed", "queued"):
        run.status = status
        with pytest.raises(ValueError, match="completed"):
            backfill.source_job(run, "character_model")
    run.status = "completed"
    run.target_node_id = 10
    with pytest.raises(ValueError, match="identity"):
        backfill.source_job(run, "character_model")


@pytest.mark.asyncio
async def test_idempotency_and_newer_publication_guards():
    session = NS(scalar=AsyncMock(side_effect=[77]))
    assert "77" in await backfill.skip_reason(session, job())
    session.scalar = AsyncMock(side_effect=[None, 1])
    assert "already contains" in await backfill.skip_reason(session, job())
    session.scalar = AsyncMock(side_effect=[None, None, 100, 101])
    assert "newer" in await backfill.skip_reason(session, job())
    session.scalar = AsyncMock(side_effect=[None, None, 100, 100])
    assert await backfill.skip_reason(session, job()) is None


@pytest.mark.asyncio
async def test_ineligible_source_does_not_create_a_new_run(monkeypatch):
    @asynccontextmanager
    async def session():
        yield NS(add=Mock())

    @asynccontextmanager
    async def connection():
        yield object()

    @asynccontextmanager
    async def lock(*_):
        yield

    monkeypatch.setattr(backfill, "SessionFactory", session)
    monkeypatch.setattr(backfill, "engine", NS(connect=connection))
    monkeypatch.setattr(backfill, "job_lock", lock)
    monkeypatch.setattr(backfill, "load_source", AsyncMock(return_value=job()))
    monkeypatch.setattr(backfill, "skip_reason", AsyncMock(return_value="newer model publication"))
    monkeypatch.setattr(backfill, "repackage_bundles", Mock())
    assert await backfill.backfill(42) is None
    backfill.repackage_bundles.assert_not_called()


def rows_for(data=b"shared"):
    digest = hashlib.sha256(data).digest()
    rows = []
    for index in (1, 2):
        metadata = {"form_id": f"mesh-{index}", "form_label": f"Form {index}",
                    "form_order": index, "game_source": {"model": "Aki/Model"}}
        for kind in ("preview", "download", "animations"):
            rows.append(("character_model_" + kind, metadata, digest, len(data), "objects/shared"))
    return rows


@pytest.mark.asyncio
async def test_downloads_are_streamed_verified_and_deduplicated(tmp_path, monkeypatch):
    calls = []

    async def get(key):
        calls.append(key)
        yield b"sh"
        yield b"ared"

    monkeypatch.setattr(backfill, "glb_summary", lambda path: {"bytes": path.stat().st_size})
    bundles = await backfill.materialize(rows_for(), tmp_path, NS(get=get))
    assert calls == ["objects/shared"]
    paths = [bundle[1] / "character.glb" for bundle in bundles]
    assert all(path.read_bytes() == b"shared" for path in paths)
    if os.name != "nt":
        assert os.path.samefile(*paths)
    assert bundles[0][3]["model"] == "Aki/Model"


@pytest.mark.asyncio
@pytest.mark.parametrize("data", [b"short", b"shared-extra", b"SHARED"])
async def test_download_integrity_failure_prevents_materialization(tmp_path, data):
    async def get(_):
        yield data
    with pytest.raises(ValueError, match="size|checksum"):
        await backfill.materialize(rows_for(), tmp_path, NS(get=get))


@pytest.mark.asyncio
async def test_rejects_inconsistent_metadata_and_missing_source_objects(tmp_path, monkeypatch):
    async def get(_):
        yield b"shared"
    rows = rows_for()
    rows[1] = (*rows[1][:1], {**rows[1][1], "form_label": "Changed"}, *rows[1][2:])
    with pytest.raises(ValueError, match="Conflicting"):
        await backfill.materialize(rows, tmp_path, NS(get=get))
    other = tmp_path / "other"
    other.mkdir()
    row = rows_for()[0]
    with pytest.raises(ValueError, match="identity"):
        await backfill.materialize([(*row[:2], None, None, None)], other, NS(get=get))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_backfill_creates_separate_run_and_publishes_atomically(tmp_path, monkeypatch, failure):
    source = job()
    created = []

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def scalar(self, query):
            return 7

        async def get(self, model, identity):
            return NS(status="completed")

        def add(self, run):
            created.append(run)

        async def flush(self):
            created[-1].id = 99

        async def commit(self):
            pass

    @asynccontextmanager
    async def connection():
        yield object()

    @asynccontextmanager
    async def lock(_, *keys):
        assert keys == (707, 9)
        yield

    from contextlib import contextmanager

    @contextmanager
    def workspace(_):
        yield tmp_path

    monkeypatch.setattr(backfill, "SessionFactory", Session)
    monkeypatch.setattr(backfill, "engine", NS(connect=connection))
    monkeypatch.setattr(backfill, "job_lock", lock)
    monkeypatch.setattr(backfill, "load_source", AsyncMock(return_value=source))
    monkeypatch.setattr(backfill, "skip_reason", AsyncMock(return_value=None))
    monkeypatch.setattr(backfill, "source_rows", AsyncMock(return_value=["source rows"]))
    monkeypatch.setattr(backfill, "get_settings", lambda: NS(s3_bucket="test"))
    monkeypatch.setattr(backfill, "S3Storage", lambda _: object())
    monkeypatch.setattr(backfill, "asset_workspace", lambda: tmp_path / "assets")
    monkeypatch.setattr(backfill, "transient_workspace", workspace)
    monkeypatch.setattr(backfill, "materialize", AsyncMock(return_value=["old bundle"]))
    repack = Mock(return_value=["new bundle"])
    monkeypatch.setattr(backfill, "repackage_bundles", repack)
    publish = AsyncMock(side_effect=ValueError("private details") if failure else None)
    monkeypatch.setattr(character_models, "publish_forms", publish)
    mark_failed = AsyncMock()
    monkeypatch.setattr(backfill, "mark_failed", mark_failed)

    if failure:
        with pytest.raises(ValueError):
            await backfill.backfill(42)
        assert mark_failed.await_args.args[0] == 99
    else:
        assert await backfill.backfill(42) == 99
        mark_failed.assert_not_awaited()
    payload = {"job_type": backfill.JOB_TYPE, "source_run_id": 42, "run_id": 99}
    assert created[0].metadata_json == {"payload": payload}
    assert created[0].status == "running"
    published_job, bundles = publish.await_args.args
    assert published_job.run_id == 99 and source.run_id == 42
    assert bundles == ["new bundle"]
    assert publish.await_args.kwargs["publication_payload"] == payload
    assert repack.call_args.args[0] == ["old bundle"]


@pytest.mark.asyncio
async def test_failure_record_is_safe_and_does_not_overwrite_completed(monkeypatch):
    run = NS(status="running", error=None, finished_at=None)

    class Session:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *_):
            pass
        scalar = AsyncMock(return_value=run)
        commit = AsyncMock()

    monkeypatch.setattr(backfill, "SessionFactory", Session)
    await backfill.mark_failed(99, ValueError("secret"))
    assert run.status == "failed" and "secret" not in run.error and "ValueError" in run.error
    run.status = "completed"
    run.error = None
    await backfill.mark_failed(99, ValueError("secret"))
    assert run.status == "completed" and run.error is None


def test_repack_adapter_result_is_accepted_for_publication(tmp_path):
    from test_model_animation_repack import _bundle

    source = _bundle(tmp_path, "mesh-1", 2)
    result = backfill.repackage_bundles([source], tmp_path / "result")
    form, output, summaries, provenance = result[0]
    assert form == source[0]
    files, count = character_models.publication_files(
        job(), output, summaries, packs=provenance["animation_packs"])
    assert count == 2
    assert {kind for kind, *_ in files} >= {"animation_manifest", provenance["animation_packs"][0]["key"]}
    assert (source[1] / "animations.glb").is_file()
