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
