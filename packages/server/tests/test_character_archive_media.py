"""Archive media queries bound storage joins and never load storage metadata."""

from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import ChunkedIteratorResult
from sqlalchemy.orm import Session

from wuwa_story.api.routes.story import character_archive as archive


@pytest.mark.asyncio
async def test_archive_media_materializes_owner_paths_and_selects_only_public_url_columns(
    monkeypatch,
):
    monkeypatch.setattr(
        archive, "S3Storage", lambda _: Row(public_url=lambda key: f"https://cdn/{key}")
    )
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("ATTACH DATABASE ':memory:' AS storage")
            # Intentionally omit metadata columns: the query must not load ORM
            # storage objects or deserialize their potentially large metadata.
            for statement in (
                "CREATE TABLE storage.file_reference (id INTEGER PRIMARY KEY, owner_node_id INTEGER, file_id INTEGER, source_path TEXT, source_name TEXT)",
                "CREATE INDEX storage.ix_media_owner ON file_reference(owner_node_id)",
                "CREATE TABLE storage.file_object (id INTEGER PRIMARY KEY, mime_type TEXT)",
                "CREATE TABLE storage.file_location (file_id INTEGER, available BOOLEAN, is_primary BOOLEAN, backend TEXT, bucket TEXT, object_key TEXT)",
                "INSERT INTO storage.file_object VALUES (1,'image/png'),(2,'audio/ogg'),(3,'text/plain')",
                "INSERT INTO storage.file_reference VALUES (1,7,1,'/Game/Icon',NULL),(2,7,2,'/Game/Voice','en'),(3,7,3,'/Game/Voice','ja'),(4,8,1,'/Game/OtherOwner',NULL),(5,7,1,'/Game/Unused',NULL),(6,7,3,'/Game/Voice','en')",
                "INSERT INTO storage.file_location VALUES (1,1,1,'s3','public','icon.png'),(2,1,1,'s3','public','voice.ogg'),(3,1,1,'s3','public','voice.wav'),(1,0,1,'s3','public','offline'),(1,1,0,'s3','public','secondary'),(1,1,1,'s3','private','private'),(1,1,1,'disk','public','disk')",
            ):
                connection.exec_driver_sql(statement)
            session = Row(
                execute=AsyncMock(side_effect=lambda query: connection.execute(query).all())
            )
            result = await archive._reference_urls(
                session,
                7,
                ["/Game/Icon", "/Game/Voice", "/Game/Icon", None, ""],
                Row(s3_bucket="public"),
            )
            assert result == {
                ("/Game/Icon", None): ("https://cdn/icon.png", "image/png"),
                ("/Game/Voice", "en"): ("https://cdn/voice.wav", "text/plain"),
                ("/Game/Voice", "ja"): ("https://cdn/voice.wav", "text/plain"),
            }
            query = session.execute.await_args.args[0]
            sql = str(
                query.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
            )
            assert "AS MATERIALIZED" in sql
            assert "file_reference.owner_node_id = 7" in sql
            assert "file_reference.source_path IN ('/Game/Icon', '/Game/Voice')" in sql
            assert "metadata" not in sql and "sha256" not in sql and "size_bytes" not in sql
            assert len(query.selected_columns) == 4
            assert "row_number() OVER" in sql and "anon_1.rank = 1" in sql
            assert "mime_type IN" not in sql  # Latest invalid MIME still wins.
            plan_query = query.compile(
                dialect=engine.dialect, compile_kwargs={"literal_binds": True}
            )
            plan = connection.exec_driver_sql(f"EXPLAIN QUERY PLAN {plan_query}").all()
            assert any("MATERIALIZE character_archive_media_candidates" in row[-1] for row in plan)
            assert any("USING INDEX ix_media_owner" in row[-1] for row in plan)
            session.execute.reset_mock()
            assert (
                await archive._reference_urls(session, 7, [None, ""], Row(s3_bucket="public")) == {}
            )
            session.execute.assert_not_awaited()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_archive_localization_ranks_numeric_versions_and_nonempty_fallback_in_sql():
    engine = create_engine("sqlite://")
    try:
        with engine.connect() as connection:
            for schema in ("ops", "i18n"):
                connection.exec_driver_sql(f"ATTACH DATABASE ':memory:' AS {schema}")
            for statement in (
                "CREATE TABLE ops.game_release (id INTEGER PRIMARY KEY, game_version TEXT, sequence INTEGER)",
                "CREATE TABLE i18n.localization_key (id INTEGER PRIMARY KEY, key TEXT)",
                "CREATE TABLE i18n.localization_content (id INTEGER PRIMARY KEY, content TEXT)",
                "CREATE TABLE i18n.localization_value (key_id INTEGER, locale_id INTEGER, release_id INTEGER, content_id INTEGER, status TEXT)",
                "INSERT INTO ops.game_release VALUES (1,'3.9',99),(2,'3.10',2),(3,'3.10',3),(4,'3.11',1)",
                "INSERT INTO i18n.localization_key VALUES (1,'name'),(2,'fallback'),(3,'blank'),(4,'other_locale')",
                "INSERT INTO i18n.localization_content VALUES (1,'old'),(2,'current'),(3,'reimported'),(4,'future'),(5,''),(6,'wrong locale'),(7,'unresolved')",
                "INSERT INTO i18n.localization_value VALUES (1,2,1,1,'resolved_nonempty'),(1,2,2,2,'resolved_nonempty'),(1,2,3,3,'resolved_nonempty'),(1,2,4,4,'resolved_nonempty'),(2,2,1,1,'resolved_nonempty'),(2,2,3,5,'resolved_nonempty'),(2,2,2,7,'missing_key'),(3,2,3,5,'resolved_nonempty'),(4,3,3,6,'resolved_nonempty')",
            ):
                connection.exec_driver_sql(statement)
            with Session(bind=connection) as orm:
                results = []

                def execute(query):
                    result = orm.execute(query)
                    results.append(result)
                    return result

                session = Row(
                    scalar=AsyncMock(return_value=2),
                    get=AsyncMock(return_value=Row(game_version="3.10")),
                    execute=AsyncMock(side_effect=execute),
                )
                result = await archive._localized_strings(
                    session, 2, "ja", ["name", "fallback", "blank", "other_locale", None]
                )
                # Keep real ORM Results: dict(result) mistakes Result.keys for a
                # mapping protocol and fails, although dict(a_fake_list) works.
                assert len(results) == 2
                assert all(isinstance(rowset, ChunkedIteratorResult) for rowset in results)
                # Numeric version and same-version reimport precedence; empty
                # values still fall back and future/locales stay excluded.
                assert result == {"name": "reimported", "fallback": "old"}
                query = session.execute.await_args.args[0]
                sql = str(
                    query.compile(
                        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                    )
                )
                assert "localization_value.release_id IN (1, 2, 3)" in sql
                assert "localization_content.content != ''" in sql
                assert "JOIN ops.game_release" not in sql
                assert len(query.selected_columns) == 2
    finally:
        engine.dispose()
