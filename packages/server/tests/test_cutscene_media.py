from types import SimpleNamespace

import pytest

from wuwa_story.api.routes.story import media


@pytest.mark.asyncio
async def test_latest_exact_asset_file_keeps_recording_version(monkeypatch):
    monkeypatch.setattr(media, 'get_settings', lambda: SimpleNamespace(s3_bucket='sample'))
    monkeypatch.setattr(media, 'S3Storage', lambda _: SimpleNamespace(public_url=lambda key: 'https://storage.example/' + key))

    class Session:
        async def execute(self, query):
            assert query.compile().params['owner_node_id_1'] == [5]
            return [(SimpleNamespace(owner_node_id=5, metadata_json={'asset_version': version, 'has_audio': True, 'soundtrack': 'music_and_effects'}), key)
                    for version, key in [('3.7.0', 'latest.mp4'), ('3.6.0', 'older.mp4')]]

    result = await media.cutscene_videos(Session(), [5])
    assert result[5]['url'].endswith('/latest.mp4')
    assert result[5]['asset_version'] == '3.7.0'
    assert result[5]['has_audio']
    assert not result[5]['subtitles_included']
    assert await media.cutscene_videos(Session(), []) == {}
