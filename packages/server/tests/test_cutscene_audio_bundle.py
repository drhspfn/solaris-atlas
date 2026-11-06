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


def test_same_language_has_distinct_rover_stems():
    stems = [AudioStem(role="voice", language="en", rover=gender, path=f"{gender}.wav",
                       sources=["event.bnk"], evidence="authored state") for gender in ("female", "male")]
    recipe = CutsceneAudioRecipe(asset="asset:ue:/Game/A.A", asset_version="3.7.1", duration=5, stems=stems)
    assert len(recipe.stems) == 2
    with pytest.raises(ValueError, match="one stem"):
        CutsceneAudioRecipe(asset=recipe.asset, asset_version=recipe.asset_version,
                            duration=5, stems=[*stems, stems[0]])


@pytest.mark.parametrize('role', ['music', 'effects', 'mixed'])
@pytest.mark.parametrize('language', ['en', 'ja', 'ko', 'zh', None])
def test_nonvoice_stems_preserve_authored_language(role, language):
    stem = AudioStem(role=role, language=language, path='a.wav', sources=['a.bnk'], evidence='native role')
    assert stem.role == role and stem.language == language


def test_full_localized_rover_role_matrix_and_selector_uniqueness():
    stems = [AudioStem(role=role, language=language, rover=rover, path='a.wav',
                       sources=['a.bnk'], evidence='native role and selector')
             for role in ['voice', 'music', 'effects']
             for language in ['en', 'ja', 'ko', 'zh'] for rover in ['male', 'female']]
    recipe = CutsceneAudioRecipe(asset='asset:ue:/Game/A.A', asset_version='3.7.1', duration=5, stems=stems)
    assert len(recipe.stems) == 24
    with pytest.raises(ValueError, match='one stem'):
        CutsceneAudioRecipe(asset=recipe.asset, asset_version=recipe.asset_version,
                            duration=5, stems=[*stems, stems[0]])
    with pytest.raises(ValueError, match='at most 64'):
        CutsceneAudioRecipe(asset=recipe.asset, asset_version=recipe.asset_version,
                            duration=5, stems=[stems[0]] * 65)


def test_aggregated_frame_event_source_closure_accepts_1000_proof_files():
    sources = [f'proof-{index}.xml' for index in range(1000)]
    stem = AudioStem(role='music', language='en', path='native.wav', sources=sources,
                     evidence='aggregated frame-event banks, XML and native WAV proofs')
    assert stem.sources == sources
    with pytest.raises(ValueError, match='at most 1000'):
        AudioStem(role='music', path='native.wav', sources=[*sources, 'extra.wav'], evidence='source')


@pytest.mark.asyncio
@pytest.mark.parametrize("complete", [False, True])
@pytest.mark.parametrize("rover", [None, "female"])
async def test_bundle_requires_every_track_and_silent_video(complete, rover):
    class Session:
        async def scalars(self, query):
            assert "3.7.0" in query.compile().params.values()
            return [
                SimpleNamespace(
                    owner_node_id=1,
                    metadata_json={
                        "videos": {"full": 12},
                        "tracks": [
                            {"role": "music", "language": None, "file_id": 10},
                            {"role": "voice", "language": "ja", "file_id": 11,
                             **({"rover": rover} if rover else {})},
                        ],
                    },
                )
            ]

        async def execute(self, query):
            return SimpleNamespace(
                all=lambda: (
                    [(10, "music.ogg"), (12, "silent.mp4")]
                    + ([(11, "voice.ogg")] if complete else [])
                )
            )

    result = await audio_bundles(
        Session(),
        [1],
        "3.7.0",
        SimpleNamespace(s3_bucket="test"),
        SimpleNamespace(public_url=lambda key: key),
    )
    if complete:
        assert result[1]["videos"] == {"full": "silent.mp4"}
        assert result[1]["tracks"][1] == {"role": "voice", "language": "ja", "url": "voice.ogg",
                                           **({"rover": rover} if rover else {})}
    else:
        assert result == {}
