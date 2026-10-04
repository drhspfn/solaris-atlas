from types import SimpleNamespace

import pytest

from wuwa_story.api.routes.story.cutscene_audio import audio_bundles
from wuwa_story.ingestion.cutscene_audio import AudioStem, CutsceneAudioRecipe


def test_stem_language_and_duplicate_roles():
    with pytest.raises(ValueError):
        AudioStem(role="voice", path="a.wav", sources=["a.bnk"], evidence="source")
    music = AudioStem(role="music", path="a.wav", sources=["a.bnk"], evidence="source")
    with pytest.raises(ValueError):
        CutsceneAudioRecipe(
            asset="asset:ue:/Game/A.A", asset_version="3.7.0", duration=5, stems=[music, music]
        )


@pytest.mark.asyncio
async def test_missing_file_hides_whole_bundle_and_version_is_scoped():
    class Session:
        async def scalars(self, query):
            assert "3.7.0" in query.compile().params.values()
            return [
                SimpleNamespace(
                    owner_node_id=1,
                    metadata_json={
                        "tracks": [
                            {"role": "music", "language": None, "file_id": 10},
                            {"role": "voice", "language": "ja", "file_id": 11},
                        ]
                    },
                )
            ]

        async def execute(self, query):
            return SimpleNamespace(all=lambda: [(10, "music.ogg")])

    result = await audio_bundles(
        Session(),
        [1],
        "3.7.0",
        SimpleNamespace(s3_bucket="test"),
        SimpleNamespace(public_url=lambda key: key),
    )
    assert result == {}
