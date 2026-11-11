"""Blender NLA representation of an authored montage's linear timeline.

The original actions and section graph are retained. Section jumps, dynamic
loops, slot blending, and transition blend-in/out are not flattened into clips.
"""
import json


def create_montage_track(armature, montage, actions, fps=30):
    import bpy
    if fps <= 0:
        raise ValueError('fps must be positive')
    render = bpy.context.scene.render
    if abs(render.fps / render.fps_base - fps) > 1e-6:
        raise ValueError('Scene frame rate does not match montage frame rate')
    if armature.type != 'ARMATURE':
        raise ValueError('Montage target must be an armature')
    if any(segment['reference'] not in actions for segment in montage['segments']):
        raise ValueError('Missing montage segment action')
    data = armature.animation_data_create()
    if data.action is not None:
        raise ValueError('Clear the active action before enabling an NLA montage')
    track = data.nla_tracks.new()
    track.name = montage['name']
    try:
        for segment in montage['segments']:
            action = actions[segment['reference']]
            slots = [slot for slot in action.slots if slot.target_id_type == 'OBJECT']
            if len(action.slots) != 1 or len(slots) != 1:
                raise ValueError(f'Unsupported action slots: {action.name}')
            slots[0].name_display = armature.name
            action.use_fake_user = True
            strip = track.strips.new(action.name, round(segment['start'] * fps), action)
            strip.action_slot = slots[0]
            strip.action_frame_start = segment['anim_start'] * fps
            strip.action_frame_end = segment['anim_end'] * fps
            strip.scale = 1.0 / abs(segment['rate'])
            strip.repeat = float(segment['loops'])
            strip.use_reverse = segment['rate'] < 0
            strip.frame_start = segment['start'] * fps
            strip.frame_end = segment['end'] * fps
            strip.blend_type = 'REPLACE'
            strip.extrapolation = 'NOTHING'
            strip.influence = 1.0
            if (abs(strip.frame_start - segment['start'] * fps) > 1e-3
                    or abs(strip.frame_end - segment['end'] * fps) > 1e-3
                    or abs(strip.scale - 1.0 / abs(segment['rate'])) > 1e-5
                    or abs(strip.repeat - segment['loops']) > 1e-5):
                raise ValueError(f'Blender could not represent montage timing: {action.name}')
        armature['solaris_montage_' + montage['name']] = json.dumps(montage, separators=(',', ':'))
    except Exception:
        data.nla_tracks.remove(track)
        raise
    return track
