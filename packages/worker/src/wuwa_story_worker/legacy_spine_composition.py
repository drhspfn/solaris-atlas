"""Validate bounded legacy prefab playback against the archived source assets."""

import hashlib
import json
import math
import struct
import zipfile
from pathlib import Path

from wuwa_story_worker.lgui_composition import _map, prefab_layout, visible_layers
from wuwa_story_worker.lgui_properties import properties
from wuwa_story_worker.sequence_composition import (
    _asset_file,
    _exports,
    _read,
    _validate_archive_limits,
)

# Exact decoded shader assets checked against their authored parent and blend mode.
# Different builds must be reviewed before their materials are accepted.
_MIP = '/Game/Aki/Render/Shaders/UI/Spine/LGUI_Standard_SpineMip'
_MIP_ADD = _MIP + '_Add'
_MATERIAL_SHA = {
    _MIP: '278bf2d740f1ade06e6fb9b5d56e14260c07a18211639712ce7dbefa5e17004d',
    _MIP_ADD: '9ae1b08122ecb0b9bed12a1b428a01ed0e3d06697639e8e981f298f9b020d9eb',
}
_NORMAL = '/LGUI/LGUI_Standard'
_STANDARD_ADD = '/Game/Aki/UI/UIResources/UiCG/Materials/MI_LGUI_Standard_ADD'
_STANDARD_ADD_SHA = '98bc48ceaafdded445471c70fa143c7e1e546540353ee1ae77cd32529236399b'


def _verify_mip(archive, manifest):
    for source, expected in _MATERIAL_SHA.items():
        entry = 'decoded/files/Client/Content/' + source.removeprefix('/Game/') + '.json'
        data = _read(archive, manifest, entry)
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError('Unverified SpineMip material source')
    parent = _exports(archive, manifest, _MIP)[0]
    instance = _exports(archive, manifest, _MIP_ADD)[0]
    p, q = parent.get('Properties', {}), instance.get('Properties', {})
    if (parent.get('Type') != 'Material' or p.get('BlendMode') != 'BLEND_Translucent'
            or p.get('ShadingModel') != 'MSM_Unlit' or instance.get('Type') != 'MaterialInstanceConstant'
            or q.get('Parent', {}).get('ObjectPath') != _MIP
            or q.get('BasePropertyOverrides', {}).get('BlendMode') != 'BLEND_Additive'
            or q.get('BasePropertyOverrides', {}).get('bOverride_BlendMode') is not True):
        raise ValueError('Unsupported SpineMip material semantics')


def _verify_standard_add(archive, manifest):
    entry = 'decoded/files/Client/Content/' + _STANDARD_ADD.removeprefix('/Game/') + '.json'
    if hashlib.sha256(_read(archive, manifest, entry)).hexdigest() != _STANDARD_ADD_SHA:
        raise ValueError('Unverified standard additive material source')
    exports = _exports(archive, manifest, _STANDARD_ADD)
    if len(exports) != 1 or exports[0].get('Type') != 'MaterialInstanceConstant':
        raise ValueError('Unsupported standard additive material')
    props = exports[0].get('Properties', {})
    overrides = props.get('BasePropertyOverrides', {})
    if (props.get('Parent', {}).get('ObjectPath') != _NORMAL
            or overrides.get('BlendMode') != 'BLEND_Additive'
            or overrides.get('bOverride_BlendMode') is not True):
        raise ValueError('Unsupported standard additive material semantics')


def _blend_map(actor, layout, names):
    result = {}
    for field, mode in [('NormalBlendMaterials', 'normal'), ('AdditiveBlendMaterials', 'additive'),
                        ('MultiplyBlendMaterials', 'multiply'), ('ScreenBlendMaterials', 'screen')]:
        if field not in actor['props']:
            raise ValueError('Prefab has no explicit complete Spine blend map')
        raw, count = _map(actor['props'][field])
        if len(raw) != 8 + count * 5:
            raise ValueError('Invalid Spine material map')
        entries = {}
        for index in range(count):
            offset = 8 + index * 5
            key, ref = struct.unpack_from('<H', raw, offset)[0], struct.unpack_from('<H', raw, offset + 3)[0]
            if raw[offset + 2] != 2 or key >= len(names) or ref >= len(layout['assets']):
                raise ValueError('Unsupported Spine material reference')
            name = names[key]
            if name in entries:
                raise ValueError('Duplicate Spine clip material')
            entries[name] = layout['assets'][ref].get('ObjectPath')
        # No active clipping is supported in this renderer; default variants must
        # nonetheless be present and point to the stock materials.
        if entries.get('ELGUICanvasClipType::Rect') != '/LGUI/LGUI_Standard_RectClip' or entries.get('ELGUICanvasClipType::Texture') != '/LGUI/LGUI_Standard_TextureClip':
            raise ValueError('Unverified Spine clip materials')
        path = entries.get('ELGUICanvasClipType::None')
        if path == _NORMAL:
            result[mode] = 'normal'
        elif path in (_MIP_ADD, _STANDARD_ADD):
            result[mode] = 'additive'
        else:
            raise ValueError('Unsupported Spine blend material')
    return result


def _validate_components(export, size):
    raw = export['Properties']
    for component in raw['ComponentsSerializeData']:
        kind = raw['ReferenceClasses'][component['ClassIndex']]['ObjectName']
        props = properties(component['PropertyData'], raw['ReferenceFNames'], raw['ReferenceAssets'])
        if kind == "Class'SpineSkeletonAnimationComponent'":
            if set(props) - {'PreviewAnimation', 'trackEntries', 'Atlas', 'SkeletonData', 'CreationMethod'}:
                raise ValueError('Unsupported Spine animation component properties')
            continue
        if (kind == "Class'UISafeZone'" and props.get('AdaptionStrategy') == 'ESafeZoneAdaptionStrategy::ESZAS_NoPadding'
                and not set(props) - {'AdaptionStrategy', 'CreationMethod'}):
            continue
        if (kind == "Class'UISizeControlByAspectRatio'"
                and props.get('ControlMode') == 'EUISizeControlByAspectRatioMode::EnvelopeParent'
                and props.get('bChangeChildrenOffset') is True and props.get('bChangeChildrenSize') is True
                and not set(props) - {'ControlMode', 'AspectRatio', 'bChangeChildrenOffset', 'bChangeChildrenSize', 'CreationMethod'}
                and type(props.get('AspectRatio')) in (int, float)
                and math.isfinite(props['AspectRatio'])
                and abs(props['AspectRatio'] - size[0] / size[1]) < 0.0001):
            continue
        raise ValueError('Unsupported prefab behaviour component')


def legacy_plan(bundle: Path):
    with zipfile.ZipFile(bundle) as archive:
        infos = archive.infolist()
        _validate_archive_limits(infos, 'Legacy archive')
        manifest = json.loads(archive.read('manifest.json'))
        missing = (manifest.get('control_sources') or {}).get('missing', [])
        if not isinstance(missing, list) or any(
            not isinstance(item, dict) or item.get('status') != 'unresolved_audio'
            or item.get('code') != 'sequence_audio_unavailable' for item in missing
        ):
            raise ValueError('Legacy prefab playback cannot infer sequence controls')
        if manifest.get('controls'):
            control_entry = manifest['controls'].get('file')
            controls = json.loads(_read(archive, manifest, control_entry))
            media = controls.get('media') or {}
            sections = media.get('sections', [])
            if (controls.get('format') != 'solaris.sequence-controls.v1'
                    or set(controls) - {'format', 'media'} or not isinstance(sections, list)
                    or any(not isinstance(section, dict) or section.get('kind') != 'audio_event'
                           or section.get('scope') != 'flow'
                           or section.get('reconstruction_kind') not in {'flow_authored_music', 'levelplay_fixed_start', 'state_consumer_fixed_start'}
                           for section in sections)):
                raise ValueError('Legacy prefab playback cannot infer sequence controls')
        variants = manifest.get('variants') or []
        if len(variants) != 1:
            raise ValueError('Legacy prefab requires one unambiguous authored variant')
        variant = variants[0]
        prefab, clip = variant.get('prefab'), variant.get('animation')
        if not isinstance(prefab, str) or not isinstance(clip, str) or not clip:
            raise ValueError('Legacy prefab animation is not authored')
        clips = {clip: None}
        conflicts = set()
        if manifest.get('presentation'):
            from wuwa_story.ingestion.dungeon_context import decoded, walk
            from wuwa_story.ingestion.spine_presentation import flow_presentation
            entry = manifest['presentation'].get('file')
            score = json.loads(_read(archive, manifest, entry))
            if (score != flow_presentation(score['source'])
                    or score.get('sha256') != manifest['presentation'].get('sha256')
                    or score.get('resource_ids') != [manifest.get('resource_id')]):
                raise ValueError('Presentation does not match its authored background source')
            if any(cue.get('kind') == 'sequence' for cue in score['cues']):
                raise ValueError('Presentation requires unresolved sequence controls')
            if any(item.get('code') != 'frame_event_clock_not_decoded' for item in score['unresolved']):
                raise ValueError('Presentation has unsupported controls')
            for _, value in walk(score['source']):
                if value.get('Name') == 'PlaySpine':
                    params = decoded(value.get('Params'))
                    config = decoded(params.get('Config')) if isinstance(params, dict) else None
                    if not isinstance(config, dict) or config.get('Type') != 'Play':
                        raise ValueError('Unsupported authored PlaySpine command')
            for cue in score['cues']:
                if cue.get('kind') != 'play_animation':
                    continue
                command = cue.get('command') or {}
                name = command.get('Name')
                loop = command.get('IsLoop', False)
                if (cue.get('target') != 'active_dialogue_background' or command.get('Type') != 'Play'
                        or not isinstance(name, str) or not name or type(loop) is not bool):
                    raise ValueError('Unsupported authored PlaySpine command')
                if name in conflicts:
                    continue
                if name in clips and clips[name] is not None and clips[name] != loop:
                    clips[name] = False
                    conflicts.add(name)
                else:
                    clips[name] = loop
        clips = {name: True if loop is None else loop for name, loop in clips.items()}
        rows = [row for row in _exports(archive, manifest, prefab) if row.get('Type') == 'PrefabAsset']
        if len(rows) != 1:
            raise ValueError('Legacy prefab is not uniquely decoded')
        layout = prefab_layout(rows[0])
        _validate_components(rows[0], layout['size'])
        names = rows[0]['Properties']['ReferenceFNames']
        used = {asset.get('ObjectPath') for asset in layout['assets']}
        if _MIP_ADD in used:
            _verify_mip(archive, manifest)
        if _STANDARD_ADD in used:
            _verify_standard_add(archive, manifest)
        layers = visible_layers(layout, verified_materials={_MIP_ADD} if _MIP_ADD in used else ())
        rigs = [layer for layer in layers if 'source' in layer]
        if len(rigs) != 1:
            raise ValueError('Animated multi-rig compositing is not yet supported')
        layer = rigs[0]
        if any(layer['color'][i] != 255 for i in range(3)) or layer['opacity'] != 1:
            raise ValueError('Animated tinted or translucent UI layer is not supported')
        if any(not math.isfinite(v) or abs(v) > .5 for v in layer['origin']):
            raise ValueError('Nonzero legacy UI origin requires verified projection')
        actor = layout['actors'][layer['actor']]
        # PreviewAnimation is an editor preview; the resource variant selects
        # the actual default and is checked against the converted rig before rendering.
        blend = _blend_map(actor, layout, names)
        skeleton = _asset_file(archive, manifest, layer['source'], 'SpineSkeletonDataAsset', 'skeletonDataFileName')
        atlas = _asset_file(archive, manifest, layer['atlas'], 'SpineAtlasAsset', 'atlasFileName')
        return {'clip': clip, 'clips': clips, 'layer': layer, 'size': layout['size'], 'skeleton': skeleton,
                'atlas': atlas, 'blend': blend, 'rover': variant.get('rover'),
                'background': any(row.get('kind') == 'solid' for row in layers)}


def mark_legacy_eligible(bundle: Path):
    """Flag an eligible source for render validation without changing source assets."""
    try:
        legacy_plan(bundle)
    except (OSError, ValueError, KeyError, TypeError, IndexError, zipfile.BadZipFile):
        return False
    temp = bundle.with_name(bundle.name + '.eligibility')
    try:
        with zipfile.ZipFile(bundle) as source, zipfile.ZipFile(temp, 'w') as target:
            manifest = json.loads(source.read('manifest.json'))
            manifest['animation_import'] = {'schema_version': 1, 'display': 'static',
                'state': 'awaiting_render_validation', 'conversion_allowed': True,
                'reasons': [{'code': 'render_validation_required'}]}
            for info in source.infolist():
                if info.filename != 'manifest.json':
                    target.writestr(info, source.read(info))
            info = zipfile.ZipInfo('manifest.json', (2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(info, json.dumps(manifest, ensure_ascii=False, sort_keys=True))
        temp.replace(bundle)
    finally:
        temp.unlink(missing_ok=True)
    return True
