import wave

import pytest

from wuwa_story_worker.cutscene_audio import validate_stem


def test_stems_must_be_aligned_to_full_movie(tmp_path):
    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(b"\0" * 48000 * 4)
    validate_stem(path, 1)
    with pytest.raises(ValueError, match="complete original"):
        validate_stem(path, 10)


def test_native_fallback_keeps_one_rover_sound_but_excludes_music_and_other_languages():
    from wuwa_story_worker.cutscene_import import fallback_frame_stems
    shared = {'role': 'effects', 'language': None, 'rover': None}
    male = {'role': 'voice', 'language': 'en', 'rover': 'male'}
    female = {**male, 'rover': 'female'}
    extras = [{**male, 'role': 'music'}, {**male, 'language': 'ja'}]
    assert fallback_frame_stems([shared, male, *extras]) == [shared, male]
    assert fallback_frame_stems([shared, male, female, *extras]) == [shared]


def test_frame_offset_schema_preserves_legacy_identity_and_rejects_invalid_values():
    from wuwa_story.ingestion.cutscenes import CutsceneRecipe, FrameAudioInput

    from wuwa_story_worker.publication_provenance import recipe_identity

    args = {'bank': 'event.bnk', 'audio_directory': 'audio', 'start_seconds': 0,
            'reference': '/Game/Aki/WwiseAudio/Events/example.example', 'mode': 'Global'}
    old = FrameAudioInput.model_validate(args)
    zero = FrameAudioInput.model_validate({**args, 'source_offset_seconds': 0})
    shifted = FrameAudioInput.model_validate({**args, 'source_offset_seconds': 1.5})
    assert old.model_dump(mode='json') == zero.model_dump(mode='json')
    assert 'source_offset_seconds' not in old.model_dump(mode='json')
    assert shifted.model_dump(mode='json')['source_offset_seconds'] == 1.5
    asset = 'asset:ue:/Game/Video.Video'
    def recipe(event):
        return CutsceneRecipe.model_validate({'cutscene': 'cutscene:Movie', 'asset_version': '3.8.0',
            'videos': [{'asset': asset, 'frame_audio': [event]}],
            'flow': {'entry': 'clip', 'evidence': 'test',
                     'nodes': [{'id': 'clip', 'kind': 'clip', 'asset': asset}]}})
    assert recipe_identity(recipe(args)) == recipe_identity(recipe({**args, 'source_offset_seconds': 0}))
    assert recipe_identity(recipe(args)) != recipe_identity(recipe({**args, 'source_offset_seconds': 1.5}))
    for offset in (-1, float('inf'), float('nan'), True):
        with pytest.raises(ValueError):
            FrameAudioInput.model_validate({**args, 'source_offset_seconds': offset})


@pytest.mark.asyncio
async def test_frame_source_offset_trims_pcm_before_movie_start(tmp_path):
    import shutil
    import struct
    from pathlib import Path
    from types import SimpleNamespace

    from wuwa_story_worker.cutscene_import import localized_audio_recipe

    ffmpeg = shutil.which('ffmpeg')
    if not ffmpeg:
        pytest.skip('ffmpeg is not installed')
    source = tmp_path / 'event.wav'
    with wave.open(str(source), 'wb') as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(struct.pack('<hh', 0, 0) * 48000 +
                           struct.pack('<hh', 10000, 10000) * 48000)
    stem = {'path': source, 'sources': [source], 'role': 'voice', 'language': 'en',
            'rover': None, 'start_seconds': 0.5, 'end_seconds': 1.5, 'source_offset_seconds': 1.0}
    recipe = await localized_audio_recipe(
        SimpleNamespace(asset='asset:ue:/Game/Video.Video', soundtrack=[]),
        tmp_path / 'unused.mp4', tmp_path, tmp_path, tmp_path / 'work',
        tmp_path / 'decoder', Path(ffmpeg), 2, False, '3.7.0', frame_stems=[stem])
    with wave.open(str(tmp_path / 'work' / recipe.stems[0].path), 'rb') as stream:
        frames = stream.readframes(stream.getnframes())
    def sample(seconds):
        return struct.unpack_from('<h', frames, round(seconds * 48000) * 4)[0]
    assert abs(sample(0.25)) < 5
    assert abs(sample(0.75)) > 5000
    assert abs(sample(1.75)) < 5


@pytest.mark.asyncio
async def test_fallback_video_uses_source_offset(tmp_path, monkeypatch):
    import json
    from unittest.mock import AsyncMock

    from wuwa_story_worker import cutscene_frame_stems, cutscene_import

    asset = 'asset:ue:/Game/Video.Video'
    recipe = tmp_path / 'recipe.json'
    recipe.write_text(json.dumps({'cutscene': 'cutscene:Movie', 'asset_version': '3.8.0',
        'videos': [{'asset': asset}],
        'flow': {'entry': 'clip', 'evidence': 'test',
                 'nodes': [{'id': 'clip', 'kind': 'clip', 'asset': asset}]}}))
    wrapper = tmp_path / 'Client/Content/Video.uexp'
    wrapper.parent.mkdir(parents=True)
    wrapper.write_bytes(b'filepath://./Aki/Movies/Video.mp4\0')
    movie = tmp_path / 'Client/Content/Aki/Movies/Video.mp4'
    movie.parent.mkdir(parents=True)
    movie.write_bytes(b'video')
    stem = {'path': tmp_path / 'event.wav', 'sources': [], 'role': 'effects',
            'language': None, 'rover': None, 'start_seconds': 0.5, 'end_seconds': 1.5,
            'source_offset_seconds': 1.0}
    monkeypatch.setattr(cutscene_frame_stems, 'prepare_frame_stems', AsyncMock(return_value=[stem]))
    monkeypatch.setattr(cutscene_import, 'video_duration', lambda *_: (2, False))
    command = []

    async def capture(args, **_kwargs):
        command.extend(args)
        raise RuntimeError('captured fallback command')

    monkeypatch.setattr(cutscene_import, 'run_tool', capture)
    with pytest.raises(RuntimeError, match='captured fallback command'):
        await cutscene_import.import_cutscene_recipe(recipe, tmp_path, tmp_path, tmp_path,
            tmp_path / 'output', tmp_path / 'decoder', tmp_path / 'ffmpeg')
    filters = command[command.index('-filter_complex') + 1]
    assert 'atrim=start=1.0:duration=1.0' in filters
    assert 'adelay=500:all=1' in filters


@pytest.mark.asyncio
async def test_localized_stems_keep_languages_and_source_timing(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from wuwa_story_worker import cutscene_import
    commands = []
    for language in ("en", "ja", "ko", "zh"):
        (tmp_path / f"{language}.bnk").write_bytes(b"bank")
    wem = tmp_path / "12.wem"
    wem.write_bytes(b"wem")
    monkeypatch.setattr(cutscene_import, "bank_media_id", lambda *_: 12)
    monkeypatch.setattr(cutscene_import, "soundtrack_wem", lambda *_: wem)
    async def run(args, *_):
        commands.append(args)
        destination = args[args.index("-o") + 1] if "-o" in args else args[-1]
        Path(destination).write_bytes(b"decoded fixture")
    from pathlib import Path
    monkeypatch.setattr(cutscene_import, "run_tool", run)
    tracks = [SimpleNamespace(bank=f"{language}.bnk", media_id=None, language=language,
                               start_seconds=1.25, end_seconds=4, gain_db=-3)
              for language in ("en", "ja", "ko", "zh")]
    result = await cutscene_import.localized_audio_recipe(
        SimpleNamespace(asset="asset:ue:/Game/Video.Video", soundtrack=tracks),
        tmp_path / "unused.mp4", tmp_path, tmp_path, tmp_path / "work",
        tmp_path / "decoder", tmp_path / "ffmpeg", 5, False, "3.7.0")
    assert {stem.language for stem in result.stems} == {"en", "ja", "ko", "zh"}
    assert all(stem.role == "voice" for stem in result.stems)
    mixes = [args for args in commands if "-filter_complex" in args]
    assert len(mixes) == 4
    assert all("adelay=1250:all=1" in args[args.index("-filter_complex")+1] for args in mixes)
    assert all("volume=-3dB" in args[args.index("-filter_complex")+1] for args in mixes)
    assert all(args[args.index("-t")+1] == "5" for args in mixes)


@pytest.mark.asyncio
@pytest.mark.parametrize("language", [None, "en"])
async def test_rover_alternatives_are_never_mixed_together(tmp_path, monkeypatch, language):
    from types import SimpleNamespace

    from wuwa_story.ingestion.cutscenes import Soundtrack

    from wuwa_story_worker import cutscene_import

    tracks = [Soundtrack(bank="event.bnk", language=language, rover=gender)
              for gender in ("male", "female")]
    commands = []
    async def decode(track, *_):
        source = tmp_path / f"{track.rover}.wem"
        source.write_bytes(track.rover.encode())
        return source, [source]
    async def run(command, *_):
        commands.append(command)
        from pathlib import Path
        Path(command[-1]).write_bytes(command[command.index("-i") + 1].encode())
    monkeypatch.setattr(cutscene_import, "decode_soundtrack", decode)
    monkeypatch.setattr(cutscene_import, "run_tool", run)
    result = await cutscene_import.localized_audio_recipe(
        SimpleNamespace(asset="asset:ue:/Game/Video.Video", soundtrack=tracks),
        tmp_path / "unused.mp4", tmp_path, tmp_path, tmp_path / "work",
        tmp_path / "decoder", tmp_path / "ffmpeg", 5, False, "3.7.1")
    assert [stem.rover for stem in result.stems] == ["male", "female"]
    assert len({stem.path for stem in result.stems}) == 2
    assert len(commands) == 2
    for command, gender in zip(commands, ("male", "female"), strict=True):
        assert command.count("-i") == 1
        assert command[command.index("-i") + 1].endswith(f"{gender}.wem")


@pytest.mark.parametrize("language,rover,allowed", [(None, None, True), ("en", None, False),
                                                   (None, "female", False), ("ja", "male", False)])
def test_shared_segments_require_comparison_of_every_audio_variant(language, rover, allowed):
    from types import SimpleNamespace

    from wuwa_story_worker.cutscene_import import can_share_variant_segments

    videos = [SimpleNamespace(frame_audio=[], soundtrack=[SimpleNamespace(language=language, rover=rover)])]
    assert can_share_variant_segments(videos) is allowed
