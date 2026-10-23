import hashlib
import json
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from wuwa_story_worker import character_voice_assets, entity_media
from wuwa_story_worker.archive_index import index_voice_paths


def cooked(number):
    return [{"EventCookedData": {"EventLanguageMap": [{"Value": {"Media": [
        {"DebugName": f"{language}_vo_character_line{number}.wav", "MediaId": number * 10 + offset,
         "MediaPathName": f"Media/{number * 10 + offset}.wem"}
        for offset, language in enumerate(("en", "ja", "ko", "zh"), 1)
    ]}}]}}]


@pytest.mark.asyncio
@pytest.mark.parametrize("event_count", [2, 26])
async def test_character_mounts_scale_by_batches_and_retry_reuses_verified_cache(tmp_path, monkeypatch, event_count):
    root, voices = tmp_path / "client", tmp_path / "voices"
    for directory, identity in ((root, "client"), (voices, "voice")):
        (directory / "game").mkdir(parents=True)
        (directory / "plan.json").write_text(json.dumps({"id": identity, "version": "3.7.0"}))
        (directory / "status.json").write_text(json.dumps({"id": identity, "state": "downloaded"}))
    (root / "keys.txt").write_text("fixture")
    fmodel, converter, decoder = [tmp_path / name for name in ("fmodel", "converter", "decoder")]
    for executable in (fmodel, converter, decoder):
        executable.write_bytes(executable.name.encode())
    monkeypatch.setattr(entity_media, "tool_path", lambda _: decoder)
    paths = {number * 10 + offset: f"Client/Content/Aki/WwiseAudio_Generated/Media/{number * 10 + offset}.wem"
             for number in range(event_count) for offset in range(1, 5)}
    voice_paths = [path for identity, path in paths.items() if identity % 10 in (3, 4)]
    index_log = voices / "archive-index.log"
    index_log.write_text("".join(f"[File] {path}\n" for path in voice_paths) + f"[Done] Listed {len(voice_paths)} files\n")
    index_voice_paths(index_log)

    async def export(root, executable, asset_filter):
        assert "|" in asset_filter
        directory = root / "exports" / hashlib.sha256(asset_filter.encode()).hexdigest()[:16]
        entries = []
        for asset in asset_filter.split("|"):
            for suffix in (".uasset", ".uexp"):
                relative = "Client/Content/" + asset + suffix
                path = directory / "files" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(relative.encode())
                entries.append({"path": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        receipt = directory / "manifest.json"
        receipt.write_text(json.dumps({"job_id": "client", "extractor_sha256": hashlib.sha256(fmodel.read_bytes()).hexdigest(), "files": entries}))
        return receipt

    export_mock = AsyncMock(side_effect=export)
    monkeypatch.setattr(character_voice_assets, "export_assets", export_mock)
    calls = []

    async def tool(args, log, timeout=1800):
        calls.append(args)
        if args[0] == converter:
            packages = Path(args[args.index("-c") + 1]).read_text().splitlines()
            output = Path(args[args.index("-o") + 1])
            for package in packages:
                stem = Path(package).stem
                path = output / "Client/Content/Aki/WwiseAudio/Events" / (stem + ".json")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(cooked(int(stem.removeprefix("play_line")))))
        elif args[0] == decoder:
            with wave.open(str(args[3]), "wb") as wav:
                wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
                wav.writeframes(b"\0\0" * 80)
        elif args[-1] == "--list":
            assert args[1] == (root / "game").resolve()
            selected = [path for identity, path in paths.items() if identity % 10 in (1, 2)]
            log.write_text("".join(f"[File] {path}\n" for path in selected) + f"[Done] Listed {len(selected)} files\n")
        else:
            assert args[0] == fmodel
            for package in args[-1].split("|"):
                path = args[3] / package
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(package.encode())
            log.write_text("[Done] Extracted files\n")

    monkeypatch.setattr(entity_media, "tool", tool)
    targets = [{"kind": "voice", "path": f"/Game/Aki/WwiseAudio/Events/play_line{number}.play_line{number}", "language": language}
               for number in range(event_count) for language in ("en", "ja", "ko", "zh")]
    provenance = {}
    resolved, missing = await entity_media.build_entity_files(root, targets, fmodel, converter, voice_root=voices, provenance=provenance)
    assert len(resolved) == event_count * 4 and not missing
    # One event export, one JSON conversion, one main index, two WEM export batches.
    export_mock.assert_awaited_once()
    assert sum(args[0] == fmodel for args in calls) == 3
    assert sum(args[0] == converter for args in calls) == 1
    assert sum(args[0] == decoder for args in calls) == event_count * 4
    for target, _, _, _, originals in resolved:
        assert len(originals) == 3  # WEM plus this event's .uasset/.uexp, never the entire batch.
        evidence = provenance[(target["path"], target["language"])]
        identity = evidence["media_id"]
        assert evidence["media_package_path"] == paths[identity]
        assert evidence["media_download_id"] == ("client" if identity % 10 in (1, 2) else "voice")
    count = len(calls)
    again, missing = await entity_media.build_entity_files(root, targets, fmodel, converter, voice_root=voices)
    assert len(again) == len(resolved) and not missing
    assert len(calls) == count
    export_mock.assert_awaited_once()
    # Cache hashes are checked before reuse, so changed WEM bytes cannot produce a false hit.
    resolved[0][4][0].write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        await entity_media.build_entity_files(root, targets, fmodel, converter, voice_root=voices)


def test_numeric_index_rejects_ambiguous_exact_ids(tmp_path):
    log = tmp_path / "archive-index.log"
    log.write_text("[File] Audio/en/123.wem\n[File] Audio/ja/123.wem\n[Done] Listed 2 files\n")
    index = index_voice_paths(log)
    with pytest.raises(ValueError, match="Ambiguous"):
        character_voice_assets.numeric_paths(index, {123})
