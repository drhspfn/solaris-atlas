import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from wuwa_story.ingestion.spine_presentation import flow_presentation

from wuwa_story_worker import (
    entity_media,
    legacy_spine_composition,
    riglyph_render,
    sequence_audio_prepare,
)
from wuwa_story_worker.sequence_music_context import authored_flow_music_context


@pytest.mark.asyncio
async def test_image_only_flow_builds_presentation_without_spine_config(tmp_path, monkeypatch):
    from wuwa_story_worker import story_spine

    presentation = flow_presentation({'StateKey': 'Story_1_2', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Play.Play'}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [{'TidTalk': 'line', 'BackgroundConfig': {
            'Type': 'Image', 'ImageAsset': '/Game/Scene.Scene'}}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Stop.Stop'}}},
    ]})
    (tmp_path / 'plan.json').write_text(json.dumps({'id': 'plan', 'version': '3.7.1', 'tier': 'hd'}))
    monkeypatch.setattr(story_spine, 'export_assets', AsyncMock(side_effect=AssertionError('unexpected ConfigDB export')))
    monkeypatch.setattr(story_spine, 'prefab_images', AsyncMock(return_value=([], [])))
    monkeypatch.setattr(story_spine, 'build_icons', AsyncMock(return_value={}))
    bundle, originals = await story_spine.build_spine_bundle(
        tmp_path, None, tmp_path / 'fmodel', tmp_path / 'converter', presentation=presentation,
        sequence_metadata_revision=5)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert json.loads(archive.read('presentation.json')) == presentation
        assert manifest['controller_archive'] and manifest['variants'] == []
        assert manifest['animation_import']['conversion_allowed'] is False
        assert manifest['sequence_metadata_revision'] == 5
    assert originals == []


@pytest.mark.asyncio
async def test_unresolved_flow_audio_does_not_block_fresh_legacy_animation(tmp_path, monkeypatch):
    event = '/Game/Aki/WwiseAudio/Events/music.music'
    presentation = flow_presentation({'StateKey': 'Story_1_29', 'Actions': [
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': event}}},
        {'Name': 'ShowTalk', 'Params': {'TalkItems': [
            {'TidTalk': 'one', 'BackgroundConfig': {'Type': 'SpineImage', 'Id': 7}}]}},
        {'Name': 'PostAkEvent', 'Params': {'EventConfig': {'Type': 'Global', 'AkEvent': '/Game/Aki/WwiseAudio/Events/stop.stop'}}},
    ]})
    content = json.dumps(presentation).encode()
    manifest = {'resource_id': 7, 'asset_version': '3.7.1', 'download_id': 'plan',
                'variants': [{'prefab': '/Game/Prefab', 'animation': 'idle'}],
                'presentation': {'file': 'presentation.json', 'sha256': presentation['sha256']},
                'files': {'presentation.json': {'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}}}
    bundle = tmp_path / 'fresh.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps(manifest))
        archive.writestr('presentation.json', content)
    (tmp_path / 'plan.json').write_text(json.dumps({'id': 'plan', 'version': '3.7.1'}))
    tool = tmp_path / 'tool'
    tool.write_bytes(b'tool')
    monkeypatch.setattr(sequence_audio_prepare, 'tool_path', lambda _: tool)
    monkeypatch.setattr(entity_media, 'client_voice_exports', lambda *_: {})
    monkeypatch.setattr(sequence_audio_prepare, 'prefetch_events', AsyncMock(side_effect=ValueError('control-only event')))
    context = authored_flow_music_context({'owner': 7, 'release_id': 21, 'presentation': presentation,
                                            'presentation_sha256': presentation['sha256']},
                                           {'asset_version': '3.7.1', 'download_id': 'plan'})
    audio_bundle, _ = await sequence_audio_prepare.prepare_sequence_audio(
        tmp_path, bundle, tool, tool, soundtrack_context=context)
    with zipfile.ZipFile(audio_bundle) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert any(item.get('status') == 'unresolved_audio' for item in manifest['control_sources']['missing'])

    actor = {'components': [{'PreviewAnimation': 'idle'}], 'props': {}}
    layout = {'actors': {1: actor}, 'assets': [], 'size': [2560, 1440]}
    monkeypatch.setattr(legacy_spine_composition, '_exports', lambda *_: [{'Type': 'PrefabAsset', 'Properties': {'ReferenceFNames': []}}])
    monkeypatch.setattr(legacy_spine_composition, 'prefab_layout', lambda _: layout)
    monkeypatch.setattr(legacy_spine_composition, '_validate_components', lambda *_: None)
    monkeypatch.setattr(legacy_spine_composition, 'visible_layers', lambda *_args, **_kwargs: [
        {'source': '/Game/Rig', 'atlas': '/Game/Atlas', 'actor': 1, 'origin': [0, 0],
         'scale': .8, 'opacity': 1, 'color': [255, 255, 255]}])
    monkeypatch.setattr(legacy_spine_composition, '_blend_map', lambda *_: {'normal': 'normal'})
    monkeypatch.setattr(legacy_spine_composition, '_asset_file', lambda *_args: _args[-1])
    monkeypatch.setattr(riglyph_render, 'archive_policy', lambda _: {'conversion_allowed': False, 'reasons': []})
    monkeypatch.setattr(riglyph_render, 'asset_workspace', lambda: tmp_path / 'logs')
    def convert(_source, destination, _cli, **_kwargs):
        destination.mkdir()
        (destination / 'skeleton.ir.json').write_text(json.dumps({'skeleton': {'slots': [],
            'animations': [{'id': 'idle', 'durationSec': 1}]}}))
        (destination / 'manifest.json').write_text('{}')
    def render(args, _log, _timeout):
        Path(args[args.index('--out') + 1]).write_bytes(b'validated video')
        return {'format': 'webm', 'frameCount': int(args[args.index('--fps') + 1]),
                'probe': {'codec': 'vp9', 'alpha': True, 'decodedAlphaVerified': True,
                          'width': int(args[args.index('--width') + 1]),
                          'height': int(args[args.index('--height') + 1])}}
    monkeypatch.setattr(riglyph_render, 'convert_archive', convert)
    monkeypatch.setattr(riglyph_render, '_run', render)
    result = riglyph_render.build_animation(audio_bundle, cli=['riglyph'])
    assert result.decision['state'] == 'rendered'
    assert len(result.variants) == 1


def test_unresolved_visual_controls_still_block_legacy_render(tmp_path, monkeypatch):
    bundle = tmp_path / 'unsafe.zip'
    with zipfile.ZipFile(bundle, 'w') as archive:
        archive.writestr('manifest.json', json.dumps({'control_sources': {'missing': [
            {'status': 'unresolved_control', 'code': 'native_control_missing'}]}}))
    with pytest.raises(ValueError, match='cannot infer sequence controls'):
        legacy_spine_composition.legacy_plan(bundle)
