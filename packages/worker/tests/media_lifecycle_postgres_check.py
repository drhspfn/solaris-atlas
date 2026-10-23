"""Runnable PostgreSQL smoke check; requires an isolated WUWA_TEST_DATABASE_URL.

Schemas must exist (Base.metadata.create_all is sufficient). No game download,
object storage, or production processing runs are accessed by this check.
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from wuwa_story.db.models.ops import GameRelease, ImportRun, ProcessingRun, Processor
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference, FileType
from wuwa_story.ingestion.import_progress import ImportProgress
from wuwa_story.ingestion.media_provenance import missing_media_targets
from wuwa_story.ingestion.media_workspaces import retain_workspace

from wuwa_story_worker import media_workspace
from wuwa_story_worker.broker import JobDeferred
from wuwa_story_worker.snapshot_order import ordered_import


async def main():
    url = make_url(os.environ["WUWA_TEST_DATABASE_URL"])
    if not url.database or "validation" not in url.database:
        raise ValueError("Use an isolated database with 'validation' in its name")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    media_workspace.engine, media_workspace.SessionFactory = engine, factory
    try:
        async with factory() as session:
            processor = Processor(key="lifecycle-smoke", version="1")
            session.add(processor)
            await session.flush()
            owner = ProcessingRun(processor_id=processor.id, status="completed",
                                  metadata_json={"request": {"kind": "prepare"}})
            session.add(owner)
            await session.flush()
            child = ProcessingRun(processor_id=processor.id, status="queued",
                                  metadata_json={"request": {"parent_id": owner.id}})
            session.add(child)
            await session.commit()
            with tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                media_workspace.asset_workspace = lambda: base
                client = base / "assets" / "retained"
                (client / "game").mkdir(parents=True)
                (client / "game" / "archive.pak").write_bytes(b"retained game")
                (client / "plan.json").write_text("{}")
                (client / "status.json").write_text(json.dumps({"state": "downloaded"}))
                (client / "keys.txt").write_text("fixture")
                root = media_workspace.resolve_workspace(client, owner.id)
                (root / "scratch.wem").write_bytes(b"temporary export")
                assert not await media_workspace.cleanup_workspace(owner.id)
                child.status = "completed"
                await session.commit()
                await retain_workspace(session, owner.id)
                assert not await media_workspace.cleanup_workspace(owner.id)
                await session.commit()
                async with media_workspace.workspace_lease(owner.id):
                    assert not await media_workspace.cleanup_workspace(owner.id)
                    assert root.exists()
                assert await media_workspace.cleanup_workspace(owner.id)
                assert not root.exists()
                assert (client / "game" / "archive.pak").read_bytes() == b"retained game"
                assert not await media_workspace.cleanup_workspace(owner.id)

            first = GameRelease(sequence=1, game_version="validation-1", upstream_name="smoke")
            second = GameRelease(sequence=2, game_version="validation-2", upstream_name="smoke")
            kind = FileType(id=1, key="image")
            session.add_all([first, second, kind])
            await session.flush()
            blob = FileObject(file_type_id=kind.id, size_bytes=4)
            session.add(blob)
            await session.flush()
            location = FileLocation(file_id=blob.id, backend="fixture", bucket="test",
                                    object_key="published", available=True)
            source = {"asset_version": "3.7.0", "download_id": "a" * 64}
            game_source = {**source, "schema_version": 1, "package_path": "/Game/Test",
                           "source_files": [{"package_path": "Client/Content/Test.uasset",
                                             "sha256": "b" * 64}]}
            reference = FileReference(file_id=blob.id, release_id=first.id,
                                      reference_type="entity_image", source_path="/Game/Test",
                                      metadata_json={"game_source": game_source})
            session.add_all([location, reference])
            await session.commit()

            class Storage:
                backend, bucket = "fixture", "test"
                present = True

                async def stat(self, key):
                    return SimpleNamespace(size_bytes=4) if self.present else None

            storage = Storage()
            target = {"owner": None, "release_id": second.id, "path": "/Game/Test",
                      "language": None, "kind": "image"}
            assert not await missing_media_targets(session, [target], source=source, storage=storage)
            await session.commit()
            storage.present = False
            assert await missing_media_targets(session, [target], source=source, storage=storage) == [target]

            old = ImportRun(release_id=first.id, importer="smoke", importer_version="2")
            session.add(old)
            await session.flush()
            progress = await ImportProgress.start(session, old, b"same manifest")

            async def imported():
                return 100, 100

            assert await progress.phase("raw", imported) == (100, 100)
            new = ImportRun(release_id=first.id, importer="smoke", importer_version="2")
            session.add(new)
            await session.flush()
            resumed = await ImportProgress.start(session, new, b"same manifest")

            async def must_not_run():
                raise AssertionError("completed phase repeated")

            assert await resumed.phase("raw", must_not_run) == (100, 0)

        async with ordered_import(engine, "2.0", None):
            try:
                async with ordered_import(engine, "2.0", None):
                    raise AssertionError("concurrent writes to shared projections")
            except JobDeferred:
                pass
        print("PASS: active-child guard, execution lease, cleanup, retained archives, "
              "cross-release reuse, missing blob retry, phase resume, import exclusion")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
