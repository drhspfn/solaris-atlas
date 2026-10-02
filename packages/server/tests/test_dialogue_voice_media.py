from types import SimpleNamespace

import pytest

from wuwa_story.api.routes.story import media


@pytest.mark.asyncio
async def test_localized_tracks_keep_asset_version_and_exact_voice_link(monkeypatch):
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
            return [(SimpleNamespace(owner_node_id=5, metadata_json={"language": "ja",
                     "asset_version": "3.7.0", "duration_seconds": 2}), "ja.wav")]

    result = await media.dialogue_media(Session(), [1], 10)
    tracks = result[1]["voice_references"][0]["tracks"]
    assert tracks == [{"language": "ja", "url": "https://storage.example/ja.wav",
                       "asset_version": "3.7.0", "duration_seconds": 2}]
    assert not any(track["language"] == "en" for track in tracks)
