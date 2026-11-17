"""Real PostgreSQL cleanup regression; requires an empty disposable database."""

import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from wuwa_story.agents.cutscene_vision import wipe_cutscene_analysis
from wuwa_story.agents.scene_analysis_reader import current_scene_report
from wuwa_story.db.models.agents import AgentCall
from wuwa_story.db.models.graph import Edge
from wuwa_story.db.models.lore import LoreChunk
from wuwa_story.db.models.ops import ProcessingRun, Processor
from wuwa_story.db.models.storage import FileLocation, FileObject, FileReference, FileVariant

DATABASE = os.getenv("WUWA_DEDICATED_EMPTY_TEST_DATABASE_URL")
pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not DATABASE, reason="Dedicated empty PostgreSQL required")]


async def test_root_cleanup_keeps_shared_objects_foreign_references_and_variants():
    engine = create_async_engine(DATABASE)
    storage = SimpleNamespace(delete=AsyncMock())
    plan = {"version": 1, "cutscene_node_id": 15537,
            "segments": [{"asset_version": "3.8.0", "source_sha256": "a" * 64}]}
    models = (Processor, ProcessingRun, AgentCall, FileObject, FileLocation,
              FileReference, FileVariant, Edge, LoreChunk)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                for schema in ("ops", "storage", "graph", "search"):
                    if await connection.scalar(text("SELECT to_regnamespace(:name)"), {"name": schema}):
                        pytest.fail("Refusing to create fixture schemas in a non-empty database")
                    await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                for model in models:
                    table = model.__table__
                    columns = [f'"{col.name}" {col.type.compile(dialect=connection.dialect)}'
                               for col in table.columns]
                    primary = [f'"{col.name}"' for col in table.primary_key.columns]
                    if primary:
                        columns.append(f'PRIMARY KEY ({", ".join(primary)})')
                    await connection.execute(text(f'CREATE TABLE "{table.schema}"."{table.name}"'
                                                  f' ({", ".join(columns)})'))
                for sql in [
                    "ALTER TABLE storage.file_variant ADD FOREIGN KEY (processor_run_id) "
                    "REFERENCES ops.processing_run(id) ON DELETE SET NULL",
                    "ALTER TABLE storage.file_variant ADD FOREIGN KEY (child_file_id) "
                    "REFERENCES storage.file_object(id) ON DELETE CASCADE",
                    "ALTER TABLE storage.file_reference ADD FOREIGN KEY (file_id) "
                    "REFERENCES storage.file_object(id) ON DELETE SET NULL",
                    "ALTER TABLE storage.file_location ADD FOREIGN KEY (file_id) "
                    "REFERENCES storage.file_object(id) ON DELETE CASCADE",
                ]:
                    await connection.execute(text(sql))
                for sql in [
                    "INSERT INTO ops.processor (id,key) VALUES (1,'cutscene_vision')",
                    "INSERT INTO ops.processing_run (id,processor_id,target_node_id,metadata) VALUES "
                    "(10,1,15537,'{\"asset_version\":\"3.8.0\"}'),"
                    "(11,1,15538,'{\"asset_version\":\"3.8.0\"}'),"
                    "(12,1,15537,'{\"asset_version\":\"3.7.0\"}')",
                    "INSERT INTO storage.file_object (id) VALUES (1),(2),(3),(4)",
                    "INSERT INTO storage.file_location (id,file_id,backend,object_key) VALUES (1,2,'s3','shared')",
                    "INSERT INTO storage.file_reference (id,owner_node_id,file_id,reference_type,metadata) VALUES "
                    "(100,15537,2,'cutscene_visual_description',:report),"
                    "(101,15537,3,'cutscene_preview','{\"asset_version\":\"3.8.0\"}'),"
                    "(102,15538,2,'cutscene_visual_description','{\"asset_version\":\"3.8.0\"}'),"
                    "(103,15537,4,'cutscene_visual_description','{\"asset_version\":\"3.7.0\"}')",
                    "INSERT INTO storage.file_variant (parent_file_id,child_file_id,variant_type,processor_run_id) VALUES "
                    "(1,2,'video_frame_atlas',10),(1,3,'cutscene_preview',10),"
                    "(4,2,'video_frame_atlas',11),(4,3,'cutscene_preview',12)",
                    "INSERT INTO graph.edge (id,from_node_id,processor_run_id,layer,basis) VALUES "
                    "(1,15537,10,'semantic','inference'),(2,15537,11,'semantic','inference'),"
                    "(3,15537,10,'source','authored')",
                    "INSERT INTO search.lore_chunk (id,source_type,source_id,game_version) VALUES "
                    "(1,'cutscene_vision','15537','3.8.0'),(2,'cutscene_vision','15537','3.7.0')",
                ]:
                    await connection.execute(text(sql), {"report": json.dumps({"asset_version": "3.8.0",
                        "complete": True, "cutscene_node_id": 15537, "analysis_plan": plan})})
                async with AsyncSession(connection, expire_on_commit=False) as session:
                    assert await current_scene_report(session, 15537, "3.8.0", plan) is not None
                    result = await wipe_cutscene_analysis(session, storage, 15537, "3.8.0")
                    assert result["deleted_runs"] == 1 and result["deleted_references"] == 2
                    assert result["deleted_files"] == result["deleted_s3_objects"] == 0
                    assert await current_scene_report(session, 15537, "3.8.0", plan) is None
                    for sql, expected in [
                        ("SELECT id FROM ops.processing_run ORDER BY id", [11, 12]),
                        ("SELECT id FROM storage.file_reference ORDER BY id", [102, 103]),
                        ("SELECT processor_run_id FROM storage.file_variant ORDER BY processor_run_id", [11, 12]),
                        ("SELECT id FROM storage.file_object ORDER BY id", [1, 2, 3, 4]),
                        ("SELECT id FROM storage.file_location ORDER BY id", [1]),
                        ("SELECT id FROM graph.edge ORDER BY id", [2, 3]),
                        ("SELECT id FROM search.lore_chunk ORDER BY id", [2]),
                    ]:
                        assert list(await session.scalars(text(sql))) == expected
                    storage.delete.assert_not_awaited()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
