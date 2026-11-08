import hashlib
import json
import zipfile

import pytest

from wuwa_story.ingestion.spine_presentation import flow_presentation
from wuwa_story_worker import legacy_spine_composition as legacy


def fixture(tmp_path, monkeypatch, *, requested='gesture', resource_id=7, bundle_id=7, raw=None):
    score = flow_presentation(raw or {'Actions': [{'Name': 'ShowTalk', 'Params': {'TalkItems': [
        {'Id': 1, 'BackgroundConfig': {'Type': 'SpineImage', 'Id': resource_id},
         'Actions': [{'Name': 'PlaySpine', 'Params': {'Config': {
             'Type': 'Play', 'Name': requested, 'IsLoop': False}}}]}]}}]})
    data = json.dumps(score).encode()
    manifest = {'resource_id': bundle_id, 'asset_version': '3.7.1', 'download_id': 'base-plan',
                'variants': [{'prefab': '/Game/Prefab', 'animation': 'idle'}],
                'presentation': {'file': 'presentation.json', 'sha256': score['sha256']},
                'files': {'presentation.json': {'size_bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}}}
    path = tmp_path / 'source.zip'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest)); z.writestr('presentation.json', data)
    actor = {'components': [{'PreviewAnimation': 'idle'}], 'props': {}}
    layout = {'actors': {1: actor}, 'assets': [], 'size': [2560, 1440]}
    monkeypatch.setattr(legacy, '_exports', lambda *_: [{'Type': 'PrefabAsset', 'Properties': {'ReferenceFNames': []}}])
    monkeypatch.setattr(legacy, 'prefab_layout', lambda _: layout)
    monkeypatch.setattr(legacy, '_validate_components', lambda *_: None)
    monkeypatch.setattr(legacy, 'visible_layers', lambda *_args, **_kwargs: [
        {'source': '/Game/Rig', 'atlas': '/Game/Atlas', 'actor': 1, 'origin': [0, 0],
         'scale': .8, 'opacity': 1, 'color': [255, 255, 255]}])
    monkeypatch.setattr(legacy, '_blend_map', lambda *_: {'normal': 'normal', 'additive': 'additive',
                                                        'multiply': 'normal', 'screen': 'normal'})
    monkeypatch.setattr(legacy, '_asset_file', lambda *_args: _args[-1])
    return path


def test_legacy_presentation_uses_exact_background_and_requested_clip(tmp_path, monkeypatch):
    path = fixture(tmp_path, monkeypatch)
    result = legacy.legacy_plan(path)
    assert result['clip'] == 'idle'
    assert result['clips'] == {'idle': True, 'gesture': False}
    assert legacy.mark_legacy_eligible(path)
    with zipfile.ZipFile(path) as archive:
        assert json.loads(archive.read('manifest.json'))['animation_import']['conversion_allowed']


def test_unrelated_background_never_becomes_eligible(tmp_path, monkeypatch):
    path = fixture(tmp_path, monkeypatch, resource_id=8)
    with pytest.raises(ValueError, match='authored background'):
        legacy.legacy_plan(path)
    assert not legacy.mark_legacy_eligible(path)


def test_same_animation_with_different_loop_cues_stays_playable(tmp_path, monkeypatch):
    def play(loop):
        return {'Name': 'PlaySpine', 'Params': {'Config': {'Type': 'Play', 'Name': 'idle', 'IsLoop': loop}}}
    raw = {'Actions': [{'Name': 'ShowTalk', 'Params': {'TalkItems': [
        {'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}, 'Actions': [play(True), play(False), play(True)]},
    ]}}]}
    path = fixture(tmp_path, monkeypatch, raw=raw)
    assert legacy.legacy_plan(path)['clips'] == {'idle': False}
    with zipfile.ZipFile(path) as archive:
        score = json.loads(archive.read('presentation.json'))
    assert [cue['command']['IsLoop'] for cue in score['cues'] if cue['kind'] == 'play_animation'] == [True, False, True]


def test_changed_presentation_hash_is_rejected(tmp_path, monkeypatch):
    path = fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(path) as z:
        entries = {name: z.read(name) for name in z.namelist()}
    manifest = json.loads(entries['manifest.json'])
    manifest['presentation']['sha256'] = 'bad'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('manifest.json', json.dumps(manifest)); z.writestr('presentation.json', entries['presentation.json'])
    with pytest.raises(ValueError, match='authored background'):
        legacy.legacy_plan(path)


def test_mip_material_requires_exact_verified_asset(tmp_path, monkeypatch):
    monkeypatch.setattr(legacy, '_read', lambda *_: b'different shader')
    with pytest.raises(ValueError, match='Unverified SpineMip'):
        legacy._verify_mip(None, {})


def test_real_denia_showtalk_background_stops_at_authored_image_change(tmp_path, monkeypatch):
    # Reduced fields from flow 剧情_3_3_达尼亚洞穴主线_1_29, keeping the original item positions.
    items = [
        {'Id': 1, 'TidTalk': 'Main_LahaiRoi_ZRJDYKX_65_1'},
        {'Id': 2, 'TidTalk': 'Main_LahaiRoi_ZRJDYKX_65_3',
         'BackgroundConfig': {'Id': 850007, 'IsLoop': True, 'Type': 'SpineImage'}},
        {'Id': 3}, {'Id': 4}, {'Id': 5}, {'Id': 6},
        {'Id': 7, 'BackgroundConfig': {'Type': 'ImageByMcGender',
            'ImageAssetMale': '/Game/Aki/UI/UIResources/Common/Image/BgScene/03_3/T_Main_03_03_C_03_Male.T_Main_03_03_C_03_Male',
            'ImageAssetFemale': '/Game/Aki/UI/UIResources/Common/Image/BgScene/03_3/T_Main_03_03_C_03_Female.T_Main_03_03_C_03_Female'}},
    ]
    raw = {'Actions': [{}] * 6 + [{'ActionId': 5000066, 'Name': 'ShowTalk', 'Params': {'TalkItems': items}}]}
    path = fixture(tmp_path, monkeypatch, bundle_id=850007, raw=raw)
    plan = legacy.legacy_plan(path)
    assert plan['clips'] == {'idle': True}
    with zipfile.ZipFile(path) as archive:
        score = json.loads(archive.read('presentation.json'))
    backgrounds = [cue for cue in score['cues'] if cue['kind'] == 'background']
    assert [(cue['source_path'], cue['background']['kind']) for cue in backgrounds] == [
        ('$.Actions[6].Params.TalkItems[1].BackgroundConfig', 'spine'),
        ('$.Actions[6].Params.TalkItems[6].BackgroundConfig', 'image'),
    ]
    assert not any(cue['kind'] == 'play_animation' for cue in score['cues'])

@pytest.mark.asyncio
async def test_audio_prepare_unresolved_music_does_not_block_visual_render(tmp_path, monkeypatch):
    from wuwa_story_worker import riglyph_render, sequence_audio_prepare
    from wuwa_story_worker.sequence_music_context import authored_flow_music_context

    def event(name):
        return {'Name': 'PostAkEvent', 'Params': {'EventConfig': {
            'Type': 'Global', 'AkEvent': f'/Game/Aki/WwiseAudio/Events/{name}'}}}
    raw = {'StateKey': 'source', 'Actions': [event('play_music'),
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'Id': 1, 'TidTalk': 'line1', 'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}}]}},
        event('stop_music')]}
    bundle = fixture(tmp_path, monkeypatch, raw=raw)
    with zipfile.ZipFile(bundle) as archive:
        score = json.loads(archive.read('presentation.json'))
    (tmp_path / 'plan.json').write_text(json.dumps({'version': '3.7.1', 'id': 'base-plan'}))
    context = authored_flow_music_context(
        {'owner': 1, 'release_id': 21, 'presentation': score, 'presentation_sha256': score['sha256']},
        {'asset_version': '3.7.1', 'download_id': 'base-plan'})
    def no_tool(_):
        raise ValueError('audio unavailable')
    monkeypatch.setattr(sequence_audio_prepare, 'tool_path', no_tool)
    output, _ = await sequence_audio_prepare.prepare_sequence_audio(
        tmp_path, bundle, tmp_path / 'fmodel', tmp_path / 'converter', soundtrack_context=context)
    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['control_sources']['missing'][0]['status'] == 'unresolved_audio'
    assert legacy.legacy_plan(output)['clips'] == {'idle': True}
    monkeypatch.setattr(riglyph_render, 'archive_policy', lambda _: {'conversion_allowed': False, 'reasons': []})
    monkeypatch.setattr(riglyph_render, 'asset_workspace', lambda: tmp_path)
    def convert(_source, target, _cli, **_options):
        target.mkdir()
        (target / 'manifest.json').write_text('{}')
        (target / 'skeleton.ir.json').write_text(json.dumps({'skeleton': {'slots': [],
            'animations': [{'id': 'idle', 'durationSec': 1}], 'bounds': [0, 0, 1, 1]}}))
    def run(args, log, timeout):
        from pathlib import Path
        Path(args[args.index('--out') + 1]).write_bytes(b'video')
        return {'format': 'webm', 'frameCount': int(args[args.index('--fps') + 1]), 'probe': {
            'codec': 'vp9', 'alpha': True, 'decodedAlphaVerified': True,
            'width': int(args[args.index('--width') + 1]), 'height': int(args[args.index('--height') + 1])}}
    monkeypatch.setattr(riglyph_render, 'convert_archive', convert)
    monkeypatch.setattr(riglyph_render, '_run', run)
    result = riglyph_render.build_animation(output, cli=['riglyph'])
    assert result.decision['state'] == 'rendered'
    assert result.variants[0]['animation'] == 'idle'
    with zipfile.ZipFile(output) as archive:
        assert json.loads(archive.read('manifest.json'))['control_sources']['missing'][0]['status'] == 'unresolved_audio'


def test_visual_control_errors_remain_blocking(tmp_path, monkeypatch):
    path = fixture(tmp_path, monkeypatch)
    with zipfile.ZipFile(path) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries['manifest.json'])
    manifest['control_sources'] = {'missing': [{'status': 'dependency_failed', 'code': 'missing_prefab'}]}
    with zipfile.ZipFile(path, 'w') as archive:
        for name, data in entries.items():
            archive.writestr(name, json.dumps(manifest).encode() if name == 'manifest.json' else data)
    with pytest.raises(ValueError, match='sequence controls'):
        legacy.legacy_plan(path)
