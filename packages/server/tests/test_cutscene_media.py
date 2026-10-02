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


@pytest.mark.asyncio
async def test_flow_does_not_mix_asset_builds_or_publish_missing_branch(monkeypatch):
    monkeypatch.setattr(media, "get_settings", lambda: SimpleNamespace(s3_bucket="sample"))
    monkeypatch.setattr(media, "S3Storage", lambda _: SimpleNamespace(public_url=lambda key: key))
    asset = "asset:ue:/Game/Test.Test"
    flow = {"entry": "clip", "evidence": "source", "nodes": [{"id": "clip", "kind": "clip", "asset": asset}]}
    class Session:
        def __init__(self, available):
            self.available = available
            self.calls = 0
        async def scalars(self, query):
            self.calls += 1
            return [SimpleNamespace(owner_node_id=1, metadata_json={"asset_version": "3.7.0", "flow": flow})] if self.calls == 1 else [SimpleNamespace(id=5, canonical_key=asset)]
        async def execute(self, query):
            assert "3.7.0" in query.compile().params.values()
            return [(SimpleNamespace(owner_node_id=5, metadata_json={"asset_version": "3.7.0", "has_audio": True}), "clip.mp4")] if self.available else []
    assert (await media.cutscene_flows(Session(True), [1]))[1]["media"][asset]["asset_version"] == "3.7.0"
    assert await media.cutscene_flows(Session(False), [1]) == {}
