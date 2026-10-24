from types import SimpleNamespace

import pytest

from wuwa_story.api.routes.story import media


@pytest.mark.asyncio
@pytest.mark.parametrize("rovers", [[None], ["female", "male"]])
async def test_localized_tracks_keep_asset_version_and_exact_voice_link(monkeypatch, rovers):
    async def links(*_args):
        return {1: [{"node_id": 5, "relation": "has_voice_reference",
                     "canonical_key": "voice:1", "basis": "source", "source": {}}]}

    monkeypatch.setattr(media, "_links", links)
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(s3_bucket="sample"))
    monkeypatch.setattr(media, "S3Storage", lambda _: SimpleNamespace(
        public_url=lambda key: "https://storage.example/" + key))

    class Session:
        async def scalars(self, _query):
            return [SimpleNamespace(node_id=5, plot_audio_id="1", file_name="vo_X_1",
                                    media_asset_node_id=None)]

        async def execute(self, _query):
            return [SimpleNamespace(owner_node_id=5, language="ja", rover=rover,
                                    asset_version="3.7.0", duration_seconds=2,
                                    object_key=f"ja-{rover}.wav") for rover in rovers]

    result = await media.dialogue_media(Session(), [1], 10)
    tracks = result[1]["voice_references"][0]["tracks"]
    assert tracks == [{"language": "ja", "rover": rover, "url": f"https://storage.example/ja-{rover}.wav",
                       "asset_version": "3.7.0", "duration_seconds": 2} for rover in rovers]
    assert not any(track["language"] == "en" for track in tracks)


@pytest.mark.asyncio
async def test_public_voice_query_projects_only_playback_fields():
    from unittest.mock import AsyncMock
    from sqlalchemy.dialects import postgresql
    from wuwa_story.db.repositories.voice_media import published_voice_tracks

    session = SimpleNamespace(execute=AsyncMock(return_value=[]))
    assert await published_voice_tracks(session, [], "sample") == []
    session.execute.assert_not_awaited()
    await published_voice_tracks(session, [5], "sample")
    statement = session.execute.await_args.args[0]
    assert list(statement.selected_columns.keys()) == [
        "owner_node_id", "language", "rover", "asset_version", "duration_seconds", "object_key"
    ]
    sql = str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "is_primary IS true" in sql and "available IS true" in sql
    assert "'voice_audio'" in sql and "'sample'" in sql
