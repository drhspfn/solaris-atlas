"""Attach an existing solaris-facial-v1 pack to a Blender scene.

Actions contain one KEY slot per matching Shape Keys datablock. Metadata on
each action preserves the exact clip/body association; no body action is
changed or generated. Call select_facial_action to activate one clip.
"""

import argparse
import json
import sys
from pathlib import Path


def attach_facial_pack(pack, *, bpy, strict=False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from wuwa_story_worker.model_facial import validate_facial_pack

    validate_facial_pack(pack)
    scene = bpy.context.scene
    fps = scene.render.fps / scene.render.fps_base
    if fps <= 0:
        raise ValueError("Scene frame rate must be positive")
    keys = [key for key in bpy.data.shape_keys if key.users]
    targets = {}
    for key in keys:
        for block in key.key_blocks:
            targets.setdefault(block.name.casefold(), []).append((key, block.name))
    if not targets:
        raise ValueError("Scene has no shape keys")
    # Re-running must not silently introduce duplicate/ambiguous associations.
    if any(action.get('solaris_facial_clip') is not None for action in bpy.data.actions):
        raise ValueError("Scene already contains Solaris facial actions")

    if strict:
        for clip in pack['clips']:
            missing = [track['target'] for track in clip['tracks']
                       if track['target'].casefold() not in targets]
            if missing:
                raise ValueError(f"Facial clip {clip['name']} has unmatched tracks: {missing}")
            body = clip.get('body_clip')
            if body:
                body_action = bpy.data.actions.get(body)
                slots = ([slot for slot in body_action.slots if slot.target_id_type == 'OBJECT']
                         if body_action else [])
                if len(slots) != 1:
                    raise ValueError(f"Facial clip {clip['name']} has missing or ambiguous body action: {body}")

    matched_clips = missing_tracks = key_count = 0
    for clip in pack['clips']:
        grouped = {}
        for track in clip['tracks']:
            matches = targets.get(track['target'].casefold(), [])
            if not matches:
                missing_tracks += 1
            for key, name in matches:
                grouped.setdefault(key, []).append((name, track['keys']))
        if not grouped:
            continue
        action = bpy.data.actions.new('Face: ' + clip['name'])
        action.use_fake_user = True
        action['solaris_facial_clip'] = clip['name']
        if clip.get('body_clip') is not None:
            action['solaris_body_clip'] = clip['body_clip']
        action['solaris_duration_seconds'] = clip['seconds']
        action['solaris_fps'] = fps
        layer = action.layers.new('Facial')
        strip = layer.strips.new(type='KEYFRAME')
        for key, tracks in grouped.items():
            slot = action.slots.new(id_type='KEY', name=key.name)
            if slot.name_display != key.name:
                raise ValueError(f'Shape Keys name cannot be represented by an action slot: {key.name}')
            bag = strip.channelbag(slot, ensure=True)
            for name, samples in tracks:
                curve = bag.fcurves.new(data_path=key.key_blocks[name].path_from_id('value'))
                curve.keyframe_points.add(len(samples))
                curve.keyframe_points.foreach_set('co', [v for t, value in samples for v in (t * fps, value)])
                for point in curve.keyframe_points:
                    point.interpolation = 'LINEAR'
                curve.update()
                key_count += len(samples)
        matched_clips += 1
    return {'clips': matched_clips, 'missing_tracks': missing_tracks, 'keyframes': key_count, 'fps': fps}


def select_facial_action(action, *, bpy, body_rig=None):
    """Activate all facial slots; optionally select its exactly matching body action.

    Standalone expressions never change the current body action. Frame 0 is
    time zero, matching facial.json and the browser. Callers manage looping.
    """
    if action.get('solaris_facial_clip') is None:
        raise ValueError('Not a Solaris facial action')
    body = action.get('solaris_body_clip')
    assignments = []
    for slot in action.slots:
        key = bpy.data.shape_keys.get(slot.name_display)
        if key is None:
            raise ValueError(f'Shape Keys datablock not found: {slot.name_display}')
        current = key.animation_data.action if key.animation_data else None
        if current and current.get('solaris_facial_clip') is None:
            raise ValueError(f'Shape Keys already use another action: {key.name}')
        assignments.append((key, slot))
    if body_rig is not None and body:
        body_action = bpy.data.actions.get(body)
        if body_action is None:
            raise ValueError(f'Body action not found: {body}')
        slots = [slot for slot in body_action.slots if slot.target_id_type == 'OBJECT']
        if len(slots) != 1:
            raise ValueError(f'Body action has ambiguous slots: {body}')
        body_rig.animation_data_create()
        body_rig.animation_data.action = body_action
        body_rig.animation_data.action_slot = slots[0]
    # Clear previous facial bindings on all keys, including keys absent in this clip.
    for key in bpy.data.shape_keys:
        animation = key.animation_data
        if animation and animation.action and animation.action.get('solaris_facial_clip') is not None:
            animation.action = None
            for name, value in key.get('solaris_facial_rest', {}).items():
                if key.key_blocks.get(name):
                    key.key_blocks[name].value = value
    for key, slot in assignments:
        if 'solaris_facial_rest' not in key:
            key['solaris_facial_rest'] = {block.name: block.value for block in key.key_blocks}
        key.animation_data_create()
        key.animation_data.action = action
        key.animation_data.action_slot = slot


def main():
    import bpy
    parser = argparse.ArgumentParser()
    parser.add_argument('facial')
    parser.add_argument('output')
    parser.add_argument('--strict', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    output = Path(args.output).resolve()
    if bpy.data.filepath and output == Path(bpy.data.filepath).resolve():
        raise ValueError('Output must not overwrite the input Blender file')
    with open(args.facial, encoding='utf-8') as stream:
        result = attach_facial_pack(json.load(stream), bpy=bpy, strict=args.strict)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), compress=True)
    print('FACIAL_RESULT ' + json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
