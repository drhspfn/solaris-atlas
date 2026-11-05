import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import flatbuffers
import pytest
from wuwa_story.ingestion.cutscenes import Soundtrack, VideoInput

from wuwa_story_worker import cutscene_import, release_media
from wuwa_story_worker.archive_index import index_voice_paths
from wuwa_story_worker.cutscene_caption_audio import (
    caption_voice_requests,
    has_caption_audio,
)
from wuwa_story_worker.cutscene_plan import engine_path, plan_cutscene


@pytest.fixture
def source_databases(tmp_path):
    # Exact local-client rows, not an assertion about production 3.7.1 bytes.
    data = json.loads((Path(__file__).parent / 'fixtures/caption_voice_local_3_7_0.json').read_text())
    assets = tmp_path / 'assets'
    folder = assets / 'Client/Content/Aki/ConfigDB'
    folder.mkdir(parents=True)
    config, plot = folder / 'db_cgVedio.db', folder / 'db_plot_audio.db'
    with sqlite3.connect(config) as db:
        db.executescript('CREATE TABLE videodata (CgId, BelongBranch, CgName, GirlOrBoy, BinData);'
                         'CREATE TABLE videosound (CaptionId, CgName, GirlOrBoy, BinData);'
                         'CREATE TABLE videocaption (CaptionId, CgName, BinData);')
        for table in ('videodata', 'videosound', 'videocaption'):
            for row in data[table]:
                record = {**row, 'BinData': bytes.fromhex(row['BinData_hex'])}
                del record['BinData_hex']
                columns = ','.join(record)
                db.execute(f'INSERT INTO {table} ({columns}) VALUES ({",".join("?" for _ in record)})', tuple(record.values()))
        for blob, in db.execute('SELECT BinData FROM videosound'):
            name = engine_path(blob).rsplit('.', 1)[-1]
            (assets / f'{name}.bnk').write_bytes(b'bank fixture')
    with sqlite3.connect(plot) as db:
        db.execute('CREATE TABLE plotaudio (Id TEXT PRIMARY KEY, BinData BLOB)')
        db.executemany('INSERT INTO plotaudio VALUES (?, ?)',
                       [(row['Id'], bytes.fromhex(row['BinData_hex'])) for row in data['plotaudio']])
    return assets, config, plot


def test_real_m0366_false_flags_never_authorize_caption_fallback(source_databases):
    assets, config, _ = source_databases
    with sqlite3.connect(config) as db:
        assert db.execute("SELECT count(*) FROM videocaption WHERE CgName='M0366'").fetchone()[0] == 21
    assert not has_caption_audio(config, 'M0366')
    assert caption_voice_requests(config, None, 'M0366', 0) == []
    recipe = plan_cutscene(config, assets, 'M0366', '3.7.0')
    assert len(recipe['videos']) == 1  # Both genders reference the same physical movie.
    assert all(not track.get('external_media') for video in recipe['videos'] for track in video['soundtrack'])


def test_real_enabled_captions_preserve_all_languages_and_declared_gender(source_databases):
    assets, config, plot = source_databases
    for gender in (0, 1):
        requests = caption_voice_requests(config, plot, 'M3_10_04', gender)
        assert len(requests) == 92
        assert {r['language'] for r in requests} == {'en', 'ja', 'ko', 'zh'}
        first = next(r for r in requests if r['caption_id'] == 1751 and r['language'] == 'en')
        assert first['filename'] == f'en_vo_Main_HuangLong_SYYLFML_85_32_{"F" if gender == 0 else "M"}.wem'
        assert first['start_seconds'] == pytest.approx(7.06596)
        # Caption 1752 checks gender only in EN, not JA.
        ja = next(r for r in requests if r['caption_id'] == 1752 and r['language'] == 'ja')
        assert ja['filename'] == 'ja_vo_Main_HuangLong_SYYLFML_85_1.wem'
        for request in requests:
            path = assets / 'external' / request['filename']
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(b'external media fixture')
    missing = []
    recipe = plan_cutscene(config, assets, 'M3_10_04', '3.7.0', missing_assets=missing)
    assert not missing and len(recipe['videos']) == 2
    for video in recipe['videos']:
        voices = [track for track in video['soundtrack'] if track.get('external_media')]
        assert len(voices) == 92  # More than the old 32-bank-track limit.
        assert all(track['end_seconds'] is None for track in voices)  # Text expiry does not stop speech.
        assert {track['rover'] for track in voices} == ({'female'} if '_Nvzhu.' in video['asset'] else {'male'})


def test_real_orphan_caption_ids_remain_missing_without_filename_guesses(source_databases):
    assets, config, plot = source_databases
    missing = []
    assert has_caption_audio(config, 'M3_10_08B')
    assert caption_voice_requests(config, plot, 'M3_10_08B', 0, missing_assets=missing) == []
    assert len(missing) == 2
    assert all('no exact PlotAudio.Id=' in reason for reason in missing)
    recipe = plan_cutscene(config, assets, 'M3_10_08B', '3.7.0', missing_assets=missing)
    assert not any(track.get('external_media') for video in recipe['videos'] for track in video['soundtrack'])
    assert len(missing) == 2


def _caption(key, start, duration, *, enabled=True, en_start=0, en_duration=0):
    builder = flatbuffers.Builder(128)
    text = builder.CreateString(key)
    builder.StartObject(12)
    builder.PrependInt32Slot(2, start, 0)
    builder.PrependInt32Slot(3, duration, 0)
    builder.PrependUOffsetTRelativeSlot(4, text, 0)
    builder.PrependInt32Slot(5, en_start, 0)
    builder.PrependInt32Slot(6, en_duration, 0)
    builder.PrependBoolSlot(11, enabled, False)
    builder.Finish(builder.EndObject())
    return bytes(builder.Output())


def _plot(key, filename, *, global_language=False, en_gender=False, variables=''):
    builder = flatbuffers.Builder(128)
    strings = [builder.CreateString(value) for value in (key, filename, variables)]
    builder.StartObject(11)
    for slot, value in zip((0, 2, 10), strings, strict=True):
        builder.PrependUOffsetTRelativeSlot(slot, value, 0)
    builder.PrependBoolSlot(3, global_language, False)
    builder.PrependBoolSlot(4, en_gender, False)
    builder.Finish(builder.EndObject())
    return bytes(builder.Output())


def test_global_language_is_authored_prefix_and_gender_remains_locale_specific(source_databases):
    _, config, plot = source_databases
    with sqlite3.connect(config) as db:
        db.execute('INSERT INTO videocaption VALUES (?,?,?)', (9000, 'Global', _caption('global_key', 30, 20)))
    with sqlite3.connect(plot) as db:
        db.execute('INSERT INTO plotaudio VALUES (?,?)', ('global_key', _plot('global_key', 'vo_global', global_language=True, en_gender=True)))
    requests = caption_voice_requests(config, plot, 'Global', 1)
    assert {r['language']: r['filename'] for r in requests} == {
        'en': 'gl_vo_global_M.wem', 'ja': 'gl_vo_global.wem',
        'ko': 'gl_vo_global.wem', 'zh': 'gl_vo_global.wem',
    }
    assert all(r['start_seconds'] == pytest.approx(.9999) for r in requests)


@pytest.mark.parametrize('filename', ['vo_{VarParams}', '../vo_unsafe'])
def test_runtime_template_and_unsafe_filename_are_not_guessed(source_databases, filename):
    _, config, plot = source_databases
    with sqlite3.connect(config) as db:
        db.execute('INSERT INTO videocaption VALUES (?,?,?)', (9000, 'Dynamic', _caption('dynamic_key', 30, 20)))
    with sqlite3.connect(plot) as db:
        db.execute('INSERT INTO plotaudio VALUES (?,?)', ('dynamic_key', _plot('dynamic_key', filename, variables='VarType=Global Key=UnknownWorldState')))
    missing = []
    assert caption_voice_requests(config, plot, 'Dynamic', 0, missing_assets=missing) == []
    assert missing == ['Caption 9000: unresolved or unsafe PlotAudio filename']


def test_localized_fallback_is_whole_list_not_per_caption(source_databases):
    _, config, plot = source_databases
    key = 'Main_HuangLong_SYYLFML_85_3'
    with sqlite3.connect(config) as db:
        db.executemany('INSERT INTO videocaption VALUES (?,?,?)', [
            (9001, 'Timing', _caption(key, 30, 60)),
            (9002, 'Timing', _caption(key, 90, 60, enabled=False, en_start=100, en_duration=30)),
        ])
    requests = caption_voice_requests(config, plot, 'Timing', 0)
    # EN has a localized list, so the voice caption with DurationEn=0 is omitted.
    assert {request['language'] for request in requests} == {'ja', 'ko', 'zh'}
    assert all(request['start_seconds'] == pytest.approx(.9999) for request in requests)


def test_missing_sources_and_unknown_gender_are_reported_not_substituted(source_databases):
    assets, config, plot = source_databases
    missing = []
    requests = caption_voice_requests(config, plot, 'M3_10_04', 2, missing_assets=missing)
    assert missing and all(not request['filename'].endswith(('_M.wem', '_F.wem')) for request in requests)
    missing = []
    recipe = plan_cutscene(config, assets, 'M3_10_04', '3.7.0', missing_assets=missing)
    assert missing and not any(t.get('external_media') for v in recipe['videos'] for t in v['soundtrack'])


def test_external_source_validation_and_old_bank_serialization():
    old = Soundtrack(bank='event.bnk').model_dump(mode='json')
    assert old == {'bank': 'event.bnk', 'language': None, 'media_id': None, 'rover': None,
                   'start_seconds': 0, 'end_seconds': None, 'gain_db': 0}
    with pytest.raises(ValueError, match='exactly one'):
        Soundtrack(bank='event.bnk', external_media='voice.wem')
    with pytest.raises(ValueError, match='bank media ID'):
        Soundtrack(external_media='voice.wem', media_id=1)


@pytest.mark.asyncio
async def test_caption_decode_and_mix_use_direct_wem_and_source_delay(tmp_path, monkeypatch):
    wem = tmp_path / 'voice.wem'
    wem.write_bytes(b'actual external source identity')
    work = tmp_path / 'work'
    work.mkdir()
    commands = []

    async def run(args, *_):
        commands.append(args)
        path = args[args.index('-o') + 1] if '-o' in args else args[-1]
        Path(path).write_bytes(b'controlled decoder output')

    monkeypatch.setattr(cutscene_import, 'run_tool', run)
    monkeypatch.setattr(cutscene_import, 'bank_media_id', lambda *_: pytest.fail('External source is not a numeric bank'))
    track = Soundtrack(external_media='voice.wem', language='en', start_seconds=7.06596)
    video = VideoInput(asset='asset:ue:/Game/Movie.Movie', soundtrack=[track])
    assert cutscene_import.available_soundtracks(video, tmp_path, tmp_path).soundtrack == [track]
    result = await cutscene_import.localized_audio_recipe(video, tmp_path / 'unused.mp4', tmp_path,
            tmp_path, work, tmp_path / 'decoder', tmp_path / 'ffmpeg', 30, False, '3.7.0')
    assert result.stems[0].language == 'en'
    assert commands[0][-1] == str(wem.resolve())
    mix = next(args for args in commands if '-filter_complex' in args)
    assert 'adelay=7066:all=1' in mix[mix.index('-filter_complex') + 1]
    assert 'atrim=duration=22.93404' in mix[mix.index('-filter_complex') + 1]
    # No Caption.Duration trim or VideoSound bank source selection was used.
    with pytest.raises(ValueError, match='escapes'):
        await cutscene_import.decode_soundtrack(Soundtrack(external_media='../outside.wem', language='en'),
                tmp_path, tmp_path, work, tmp_path / 'decoder', {})


@pytest.mark.asyncio
async def test_selective_extraction_includes_exact_caption_external_names(source_databases, tmp_path, monkeypatch):
    _, config, plot = source_databases
    root, voice = tmp_path / 'client', tmp_path / 'voice'
    root.mkdir()
    voice.mkdir()
    names = {r['filename'] for gender in (0, 1)
             for r in caption_voice_requests(config, plot, 'M3_10_04', gender)}
    paths = {f'Client/Content/ExternalSources/{name}' for name in names}
    log = voice / 'archive-index.log'
    log.write_text(''.join(f'[File] {path}\n' for path in paths) + f'[Done] Listed {len(paths)} files\n')
    index_voice_paths(log)
    main_log = root / 'archive-index.log'
    main_log.write_text('[Done] Listed 0 files\n')
    main_index = index_voice_paths(main_log)
    monkeypatch.setattr(release_media, 'package_root', lambda *a, **kw: voice)
    monkeypatch.setattr(release_media, 'client_root', lambda _: root)
    monkeypatch.setattr(release_media, 'tool_path', lambda _: tmp_path / 'tool')
    monkeypatch.setattr(release_media, 'ensure_archive_index', AsyncMock(return_value=main_index))
    selected = set()

    async def export(parent, batch, destination):
        selected.update(batch)
        for value in batch:
            target = destination / value
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'exported external source')

    monkeypatch.setattr(release_media, 'extract_voice_paths', export)
    destination = await release_media.extract_cutscene_audio({'voice_plan_id': 'voice'}, config, 'M3_10_04')
    assert selected == paths
    receipt = json.loads((destination.parent / 'audio-export.json').read_text())
    assert set(receipt['caption_external_media']) == {name.casefold() for name in names}
    assert len(list(destination.rglob('*.wem'))) == len(names)


@pytest.mark.asyncio
async def test_old_completed_publication_only_rechecked_when_caption_coverage_required(monkeypatch):
    legacy = SimpleNamespace(source_path='cutscene:Legacy', metadata_json={'publication_complete': True, 'game_source': {}})
    current = SimpleNamespace(source_path='cutscene:Current', metadata_json={'publication_complete': True,
                                   'game_source': {'caption_audio_schema': 1}})
    session = AsyncMock()
    factory = Mock()
    factory.return_value.__aenter__ = AsyncMock(return_value=session)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(cutscene_import, 'SessionFactory', factory)
    monkeypatch.setattr(cutscene_import, 'verified_references', AsyncMock(return_value=[legacy, current]))
    context = {'asset_version': '3.7.0', 'download_id': 'a' * 64, 'tier': 'hd', 'tools': {'decoder': 'x'}}
    names = {'cutscene:Legacy', 'cutscene:Current'}
    assert await cutscene_import.missing_cutscene_identities(names, source_context=context, storage=object()) == set()
    assert await cutscene_import.missing_cutscene_identities(names, source_context=context, storage=object(),
                                                            require_caption_audio=True) == {'cutscene:Legacy'}


@pytest.mark.asyncio
@pytest.mark.parametrize('caption_enabled', [False, True])
async def test_leaf_requires_caption_coverage_only_from_prepared_source_flags(monkeypatch, caption_enabled):
    monkeypatch.setattr(release_media, 'source_context', AsyncMock(return_value={}))
    missing = AsyncMock(return_value=set())
    monkeypatch.setattr(release_media, 'missing_cutscene_identities', missing)
    monkeypatch.setattr(release_media, 'cleanup_published_output', AsyncMock())
    monkeypatch.setattr(release_media, 'client_root', lambda _: pytest.fail('A complete publication must not remount archives'))
    parent = {'caption_voice_cg_names': ['Scene']} if caption_enabled else {}
    _, status = await release_media.cutscene({'targets': ['Scene'], 'run_id': 1}, parent)
    assert status == 'completed'
    assert missing.await_args.kwargs.get('require_caption_audio', False) is caption_enabled
    assert missing.await_args.kwargs['source_context'].get('caption_audio_schema') == (1 if caption_enabled else None)
