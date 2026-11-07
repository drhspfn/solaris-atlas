"""Compile static UI compositions from native prefab bindings and sequence keys."""

import copy
import math
import struct

from wuwa_story_worker.lgui_properties import properties, property_prefix


def _map(raw):
    data = bytes.fromhex(raw['raw'])
    if len(data) < 8:
        raise ValueError('Truncated LGUI map')
    removed, count = struct.unpack_from('<ii', data)
    if removed or not 0 <= count <= 256:
        raise ValueError('Unsupported LGUI map extent')
    return data, count


def _soft(data, names):
    index, tail = struct.unpack('<Hi', data)
    if tail or index >= len(names) or not names[index].startswith('/Game/'):
        raise ValueError('Unsupported LGUI soft object path')
    return names[index].split('.')[0]


def sequence_bindings(root, names, assets):
    sequences, bindings = {}, {}
    for field, destination in [('LevelSequences', sequences), ('LevelSequenceBindings', bindings)]:
        if field not in root:
            continue
        data, count = _map(root[field])
        offset = 8
        for _ in range(count):
            if field == 'LevelSequences':
                size, = struct.unpack_from('<i', data, offset)
                if not 1 <= size <= 1024 or offset + 4 + size > len(data):
                    raise ValueError('Invalid LGUI sequence key')
                key = data[offset + 4:offset + 3 + size].decode('utf-8')
                offset += 4 + size
            else:
                key = _soft(data[offset:offset + 6], names)
                offset += 6
            value, consumed = property_prefix(data[offset:], names, assets)
            offset += consumed
            if key in destination:
                raise ValueError('Duplicate LGUI sequence binding')
            if field == 'LevelSequences':
                path = _soft(bytes.fromhex(value['LevelSequence']['raw']), names)
                setting = properties(bytes.fromhex(value['PlaySetting']['raw']), names, assets)
                if setting.get('bRestoreState') or setting.get('bRandomStartTime'):
                    raise ValueError('Restoring/random UI sequences require runtime playback')
                destination[key] = path
            else:
                raw, length = _map(value['BindingsMap'])
                at, mapping = 8, {}
                for _ in range(length):
                    guid = '-'.join(f'{v:08X}' for v in struct.unpack_from('<IIII', raw, at))
                    at += 16
                    if raw[at] != 4:
                        raise ValueError('Expected local prefab binding reference')
                    identity, = struct.unpack_from('<I', raw, at + 1)
                    at += 5
                    if guid in mapping:
                        raise ValueError('Duplicate prefab binding GUID')
                    mapping[guid] = identity
                if at != len(raw):
                    raise ValueError('Invalid prefab binding map trailer')
                destination[key] = mapping
        if offset != len(data):
            raise ValueError('Invalid LGUI sequence map trailer')
    return sequences, bindings


def prefab_layout(export):
    p = export['Properties']
    if p.get('Version') != 3:
        raise ValueError('Unsupported LGUI prefab serialization version')
    names, assets, actors = p['ReferenceFNames'], p['ReferenceAssets'], {}
    rows = p['ActorsSerializeData']
    if not 0 < len(rows) <= 256:
        raise ValueError('LGUI composition exceeds actor bound')
    for actor in rows:
        identity = actor['FileID']
        if identity in actors:
            raise ValueError('Duplicate LGUI actor identity')
        props = properties(actor['RootComponentPropertyData'], names, assets)
        rotation = props.get('RelativeRotation', [0, 0, 0])
        if (not isinstance(rotation, list) or len(rotation) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or abs(v) > 0.0001 for v in rotation)):
            raise ValueError('Rotated UI compositions require a projected camera')
        actors[identity] = {'id': identity, 'parent': actor['ParentActorFileID'],
            'index': actor['HierarchyIndex'], 'active': actor['IsActive'], 'props': props,
            'kind': p['ReferenceClasses'][actor['RootClassIndex']]['ObjectName'], 'components': []}
    roots = [a for a in actors.values() if a['parent'] == 0]
    if len(roots) != 1 or any(a['parent'] and a['parent'] not in actors for a in actors.values()):
        raise ValueError('Incomplete LGUI actor hierarchy')
    for identity in actors:
        seen, current = set(), identity
        while current:
            if current in seen:
                raise ValueError('Cyclic LGUI actor hierarchy')
            seen.add(current)
            current = actors[current]['parent']
    for component in p['ComponentsSerializeData']:
        if component['OuterFileID'] not in actors:
            raise ValueError('LGUI component has no owning actor')
        kind = p['ReferenceClasses'][component['ClassIndex']]['ObjectName']
        if kind == "Class'SpineSkeletonAnimationComponent'":
            actors[component['OuterFileID']]['components'].append(
                properties(component['PropertyData'], names, assets))
    root = roots[0]
    size = [root['props'].get(key) for key in ('width', 'height')]
    if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v <= 16384 for v in size):
        raise ValueError('LGUI prefab has no bounded authored canvas')
    sequences, bindings = sequence_bindings(root['props'], names, assets)
    return {'actors': actors, 'root': root['id'], 'size': size,
            'sequences': sequences, 'bindings': bindings, 'assets': assets}


def sequence_tracks(exports, bindings):
    scenes = [e['Properties'] for e in exports if e.get('Type') == 'MovieScene']
    if len(scenes) != 1:
        raise ValueError('UI sequence has no unique MovieScene')
    scene = scenes[0]
    rate = scene['TickResolution']
    hz = rate['Numerator'] / rate.get('Denominator', 1)
    if not math.isfinite(hz) or hz <= 0:
        raise ValueError('Invalid UI sequence clock')
    end = scene['PlaybackRange']['Value']['UpperBound']['Value']['Value'] / hz
    possessables = {p['Guid']: p for p in scene.get('Possessables', [])}
    result = []
    def actor(guid, seen=None):
        seen = set() if seen is None else seen
        if guid in bindings:
            return bindings[guid]
        if guid in seen or guid not in possessables:
            raise ValueError('UI sequence has an unresolved prefab binding')
        seen.add(guid)
        return actor(possessables[guid]['ParentGuid'], seen)
    for binding in scene.get('ObjectBindings', []):
        for track in binding.get('Tracks', []):
            index = int(track['ObjectPath'].rsplit('.', 1)[1])
            data = exports[index]
            props = data['Properties']
            field = props.get('PropertyBinding', {}).get('PropertyName')
            if data['Type'] not in {'MovieSceneBoolTrack', 'MovieSceneFloatTrack'} or field not in {'Alpha', 'bIsUIActive'}:
                raise ValueError('Unsupported UI sequence property track')
            for section in props.get('Sections', []):
                section = exports[int(section['ObjectPath'].rsplit('.', 1)[1])]['Properties']
                curve = section['BoolCurve' if field == 'bIsUIActive' else 'FloatCurve']
                times, values = curve.get('Times', []), curve.get('Values', [])
                if not times or len(times) != len(values):
                    raise ValueError('Incomplete UI sequence channel')
                points = [(t['Value'] / hz, v if isinstance(v, bool) else v['Value'])
                          for t, v in zip(times, values, strict=True)]
                if (any(not math.isfinite(t) or type(v) not in (int, float, bool)
                        or not math.isfinite(v) for t, v in points)
                        or any(a[0] >= b[0] for a, b in zip(points, points[1:]))):
                    raise ValueError('Invalid UI channel value')
                result.append({'actor': actor(binding['ObjectGuid']), 'field': field, 'points': points})
    return end, result


def apply_settled_tracks(actors, tracks, elapsed):
    """Only sample settled keys: intermediate easing is not approximated."""
    for track in tracks:
        points = track['points']
        if elapsed < points[-1][0] and any(v != points[0][1] for _, v in points[1:]):
            raise ValueError('Static frame requested during a UI transition')
        if track['actor'] not in actors:
            raise ValueError('UI sequence binding is outside the prefab')
        actors[track['actor']]['props'][track['field']] = points[-1][1]


def visible_layers(layout, actors=None):
    actors = copy.deepcopy(actors or layout['actors'])
    result, visited = [], set()
    def visit(identity, scale, origin, opacity):
        if identity in visited:
            raise ValueError('Cyclic LGUI actor hierarchy')
        visited.add(identity)
        a, p = actors[identity], actors[identity]['props']
        if not a['active'] or p.get('bIsUIActive') is False:
            return
        color = p.get('Color', [255, 255, 255, 255])
        alpha = p.get('Alpha', 1)
        if (not isinstance(color, list) or len(color) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 255 for v in color)
                or type(alpha) not in (int, float) or not math.isfinite(alpha) or not 0 <= alpha <= 1):
            raise ValueError('Invalid UI opacity or tint')
        opacity *= color[3] / 255 * alpha
        if opacity <= 0:
            return
        location, own_scale = p.get('RelativeLocation', [0, 0, 0]), p.get('RelativeScale3D', [1, 1, 1])
        if any(not isinstance(values, list) or len(values) != 3
               or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
               for values in (location, own_scale)):
            raise ValueError('Invalid UI transform')
        # LGUI's UI plane is Unreal Y/Z (X is depth), including its letterbox actors.
        origin = [origin[i] + location[i + 1] * scale[i] for i in range(2)]
        scale = [scale[i] * own_scale[i + 1] for i in range(2)]
        if 'UISpineRenderable' in a['kind']:
            if len(a['components']) != 1 or abs(scale[0] - scale[1]) > 0.0001 or min(scale) <= 0:
                raise ValueError('Unsupported Spine component or nonuniform UI scale')
            component = a['components'][0]
            skeleton = component.get('SkeletonData', {}).get('ObjectPath')
            if not skeleton or skeleton != p.get('CurrSkeletonData', {}).get('ObjectPath'):
                raise ValueError('Unresolved current Spine skeleton binding')
            for field in ('NormalBlendMaterials', 'AdditiveBlendMaterials', 'MultiplyBlendMaterials', 'ScreenBlendMaterials'):
                if field not in p:
                    continue
                raw, count = _map(p[field])
                at = 8
                for _ in range(count):
                    if at + 5 > len(raw) or raw[at + 2] != 2:
                        raise ValueError('Unsupported UI material reference')
                    index, = struct.unpack_from('<H', raw, at + 3)
                    at += 5
                    if index >= len(layout['assets']) or layout['assets'][index]['ObjectPath'] not in {
                        '/LGUI/LGUI_Standard', '/LGUI/LGUI_Standard_RectClip', '/LGUI/LGUI_Standard_TextureClip',
                        '/Game/Aki/UI/UIResources/UiCG/Materials/MI_LGUI_Standard_ADD',
                    }:
                        raise ValueError('Custom UI material requires a compositor adapter')
                if at != len(raw):
                    raise ValueError('Invalid UI material map trailer')
            result.append({'source': skeleton, 'atlas': component.get('Atlas', {}).get('ObjectPath'),
                'scale': scale[0], 'origin': origin, 'opacity': opacity,
                'color': color[:3], 'actor': identity})
        elif 'UISprite' in a['kind']:
            if p.get('sprite', {}).get('ObjectPath') != '/Game/Aki/UI/UIResources/Common/Atlas/SP_CommonDefault':
                raise ValueError('Textured UI sprite needs an extracted image layer')
            if color[:3] != [0, 0, 0]:
                raise ValueError('Visible UI overlay is not a settled story frame')
            if result or opacity != 1:
                raise ValueError('Foreground UI masks require a sprite compositor')
        elif a['kind'] != "Class'UIItem'":
            raise ValueError('Unsupported UI render component')
        elif color[:3] != [255, 255, 255]:
            raise ValueError('Inherited UI tint requires a compositor adapter')
        for child in sorted((c for c in actors.values() if c['parent'] == identity), key=lambda c: (c['index'], c['id'])):
            visit(child['id'], scale, origin, opacity)
    visit(layout['root'], [1, 1], [0, 0], 1)
    return result
