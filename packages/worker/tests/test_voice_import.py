import asyncio
import subprocess
import wave
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from wuwa_story_worker import voice_import


@pytest.fixture
def importer(tmp_path, monkeypatch):
    """Model owner transaction locks and persisted identities, with observable I/O."""
    state = SimpleNamespace(
        voices=[SimpleNamespace(node_id=1, file_name="vo_test")],
        sessions=[], locks={}, refs={}, events=[], prepared=[],
    )
    current_session = ContextVar("voice_import_session")

    class Session:
        active = False
        owner = None

        async def __aenter__(self):
            state.sessions.append(self)
            current_session.set(self)
            return self

        async def __aexit__(self, *_):
            await self.commit()

        async def scalars(self, query):
            self.active = True
            return list(state.voices)

        async def execute(self, query, params):
            owner = params["id"]
            await state.locks.setdefault(owner, asyncio.Lock()).acquire()
            self.active, self.owner = True, owner
            state.events.append(("lock", owner))

        async def scalar(self, query):
            assert self.owner is not None
            name = query.compile().params["source_name_1"]
            state.events.append(("lookup", self.owner, name))
            return state.refs.get((self.owner, name))

        async def commit(self):
            self.active = False
            if self.owner is not None:
                state.events.append(("commit", self.owner))
                state.locks[self.owner].release()
                self.owner = None

    monkeypatch.setattr(voice_import, "SessionFactory", Session)
    monkeypatch.setattr(voice_import, "get_settings", lambda: None)
    monkeypatch.setattr(voice_import, "S3Storage", lambda _: AsyncMock())
    monkeypatch.setattr(voice_import, "verified_references", AsyncMock(return_value=[]))

    def decode(args, **kwargs):
        assert not current_session.get().active
        state.events.append(("decode", Path(args[-1]).name))
        with wave.open(args[3], "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            audio.writeframes(b"\0\0" * 480)

    state.decoder = Mock(side_effect=decode)
    monkeypatch.setattr(voice_import.subprocess, "run", state.decoder)

    async def prepare(path, mime_type=None):
        assert not current_session.get().active
        result = SimpleNamespace(path=path, mime_type=mime_type)
        state.prepared.append(result)
        state.events.append(("prepare", path.name))
        return result

    async def register_file(session, path, kind, *, mime_type, prepared):
        assert session.owner is not None
        assert any(prepared is item for item in state.prepared)
        assert prepared.path == path and prepared.mime_type == mime_type
        state.events.append(("register", path.name))
        await asyncio.sleep(0)  # Let another importer contend for the owner lock.
        return SimpleNamespace(id=10 if kind == "audio_wem" else 11)

    async def register_reference(session, **kwargs):
        key = kwargs["owner_node_id"], kwargs["source_name"]
        assert session.owner == key[0] and key not in state.refs
        ref = SimpleNamespace(**kwargs)
        state.refs[key] = ref
        return ref

    state.service = SimpleNamespace(
        prepare_file=AsyncMock(side_effect=prepare),
        register_file=AsyncMock(side_effect=register_file),
        register_variant=AsyncMock(return_value=SimpleNamespace()),
        register_reference=AsyncMock(side_effect=register_reference),
    )
    monkeypatch.setattr(voice_import, "FileRegistrationService", lambda _: state.service)
    state.paths = {}
    for language in ("en", "ja"):
        path = tmp_path / f"{language}_vo_test.wem"
        path.write_bytes(b"voice")
        state.paths[language, None] = path

    async def run(exports=None, **kwargs):
        return await voice_import.import_voice_sample(
            tmp_path, tmp_path / "decoder", "3.7.0", require_all_languages=False,
            grouped={"vo_test": state.paths if exports is None else exports}, **kwargs)

    state.run = run
    return state


async def test_all_tracks_are_prepared_before_one_owner_transaction(importer):
    importer.voices.append(SimpleNamespace(node_id=2, file_name="vo_test"))
    result = await importer.run()
    assert result["tracks"] == 4
    assert importer.decoder.call_count == 2
    assert importer.service.prepare_file.await_count == 4
    assert importer.service.register_file.await_count == 4
    assert [event for event in importer.events if event[0] == "lock"] == [("lock", 1), ("lock", 2)]
    first_lock = importer.events.index(("lock", 1))
    assert all(index < first_lock for index, event in enumerate(importer.events)
               if event[0] in {"prepare", "decode"})
    assert len(importer.refs) == 4
    for ref in importer.refs.values():
        assert ref.file_id == 11
        assert ref.metadata_json["publication_file_ids"] == [10, 11]
        assert len(ref.metadata_json["voice_tracks"]) == 2
        assert ref.metadata_json["duration_seconds"] == 0.01


@pytest.mark.parametrize("failure", ["prepare", "decode"])
async def test_external_failure_cannot_publish_partial_owner(importer, failure):
    if failure == "prepare":
        # Fail on the second track, after the first track's preparation succeeded.
        original = importer.service.prepare_file.side_effect

        async def fail(path, mime_type=None):
            if path.name.startswith("ja_"):
                raise OSError("storage unavailable")
            return await original(path, mime_type)

        importer.service.prepare_file.side_effect = fail
        error = OSError
    else:
        original = importer.decoder.side_effect

        def fail(args, **kwargs):
            if Path(args[-1]).name.startswith("ja_"):
                Path(args[3]).write_bytes(b"partial")
                raise subprocess.TimeoutExpired(args, 120)
            return original(args, **kwargs)

        importer.decoder.side_effect = fail
        error = subprocess.TimeoutExpired
    with pytest.raises(error):
        await importer.run()
    assert not importer.refs
    assert not any(event[0] == "lock" for event in importer.events)
    importer.service.register_file.assert_not_awaited()
    assert not list(next(iter(importer.paths.values())).parent.glob("*.partial.wav"))
    if failure == "prepare":
        importer.service.prepare_file.side_effect = original
    else:
        importer.decoder.side_effect = original
    assert (await importer.run())["tracks"] == 2
    assert len(importer.refs) == 2


async def test_concurrent_retries_recheck_identity_after_owner_lock(importer):
    # Independent jobs use distinct workspaces but publish the same owner identity.
    importer.paths = {key: path for key, path in importer.paths.items() if key[0] == "en"}
    source = importer.paths["en", None]
    other = source.parent / "other" / source.name
    other.parent.mkdir()
    other.write_bytes(source.read_bytes())
    result = await asyncio.gather(importer.run(), importer.run({("en", None): other}))
    assert [item["tracks"] for item in result] == [1, 1]
    assert len(importer.refs) == 1
    assert importer.service.register_reference.await_count == 1
    assert [event[0] for event in importer.events if event[0] in {"lock", "lookup", "commit"}] == [
        "lock", "lookup", "commit", "lock", "lookup", "commit",
    ]


async def test_verified_donor_needs_no_preparation_and_keeps_authored_roster(importer, monkeypatch):
    importer.voices = [SimpleNamespace(node_id=2, file_name="vo_test")]
    importer.paths = {key: path for key, path in importer.paths.items() if key[0] == "en"}
    path = importer.paths["en", None]
    context = {"asset_version": "3.7.0", "download_id": "a" * 64, "tier": "hd",
               "tools": {"WUWA_VGMSTREAM_PATH": "b" * 64}}
    source_path = voice_import.voice_package_path(path.parent, path, context)
    context["voice_paths"] = [source_path.replace("en_", f"{language}_")
                              for language in ("en", "ja", "ko", "zh")]
    provenance = voice_import.publication_source(
        context, {"path": source_path, "kind": "voice", "language": "en"}, [path],
        voice_name="vo_test", rover=None)
    provenance["source_files"][0]["package_path"] = source_path
    donor = SimpleNamespace(
        owner_node_id=1, source_path=source_path, source_name=path.name, file_id=11,
        metadata_json={"voice_name": "vo_test", "language": "en", "rover": None,
                       "asset_version": "3.7.0", "game_source": provenance,
                       "publication_file_ids": [10, 11], "original_file_id": 10})
    verified = AsyncMock(return_value=[donor])
    monkeypatch.setattr(voice_import, "verified_references", verified)
    result = await importer.run(source_context=context)
    assert result["tracks"] == 1
    assert set(result["missing_voices"]) == set(context["voice_paths"]) - {source_path}
    importer.decoder.assert_not_called()
    importer.service.prepare_file.assert_not_awaited()
    importer.service.register_file.assert_not_awaited()
    verified.assert_awaited_once()
    ref = importer.refs[2, path.name]
    assert ref.metadata_json["game_source"] == provenance
    assert ref.metadata_json["publication_file_ids"] == [10, 11]
    assert len(ref.metadata_json["voice_tracks"]) == 4
    assert ref.metadata_json["voice_inventory_authoritative"]
    assert not ref.metadata_json["voice_inventory_complete"]
