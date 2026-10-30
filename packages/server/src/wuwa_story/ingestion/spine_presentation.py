"""Retain the game-authored score separately from the skeletal animation format.

Source paths describe structure, not a playthrough. In particular a pose of zero
duration can be held until an external sequence, dialogue or QTE cue advances it.
"""

import hashlib
import json

from wuwa_story.ingestion.dungeon_context import decoded, walk
from wuwa_story.ingestion.talk_backgrounds import background_config

SPINE_RECIPE = 2


def presentation_hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def flow_presentation(raw):
    """Keep exact commands and their surrounding branches for later compilation.

    Never join a numeric montage/face-expression ID to a background resource ID,
    or derive timing/next clips from animation names such as ``1to2``.
    """
    source = {key: decoded(value) for key, value in raw.items()}
    cues, resources, unresolved = [], set(), []
    for path, value in walk(source):
        background = background_config(value.get('BackgroundConfig'))
        if background is not None:
            cues.append({'kind': 'background', 'source_path': path + '.BackgroundConfig',
                         'background': background})
            if background['kind'] == 'spine':
                resources.add(background['resource_id'])
        name, params = value.get('Name'), decoded(value.get('Params'))
        if name == 'PlaySpine' and isinstance(params, dict):
            config = decoded(params.get('Config'))
            if isinstance(config, dict) and config.get('Type') == 'Play':
                cues.append({'kind': 'play_animation', 'source_path': path + '.Params.Config',
                             'target': 'active_dialogue_background', 'command': config})
        if name in {'PostAkEvent', 'SetAudioState'} and isinstance(params, dict):
            cues.append({'kind': 'audio_action', 'source_path': path, 'command': value})
        if name == 'PlaySequenceData' and isinstance(params, dict):
            asset = params.get('Path')
            if isinstance(asset, str) and asset.startswith('/Game/'):
                cues.append({'kind': 'sequence', 'source_path': path + '.Params.Path', 'asset': asset})
                unresolved.append({'code': 'sequence_control_not_decoded', 'source_path': path,
                                   'asset': asset})
        # TalkItems are identified by their authored container, not by a text or ID match.
        items = decoded(value.get('TalkItems'))
        for index, item in enumerate(items if isinstance(items, list) else []):
            if not isinstance(item, dict):
                continue
            cues.append({'kind': 'dialogue', 'source_path': f'{path}.TalkItems[{index}]',
                         'talk_id': item.get('Id'), 'text_key': item.get('TidTalk'),
                         'speaker_id': item.get('WhoId'), 'command': item})
        events = decoded(value.get('FrameEvents'))
        if events:
            cues.append({'kind': 'frame_events', 'source_path': path + '.FrameEvents',
                         'command': events})
            unresolved.append({'code': 'frame_event_clock_not_decoded',
                               'source_path': path + '.FrameEvents'})
    result = {'format': 'solaris.story-presentation.v1', 'ordering': 'authored_structure',
              'timing_status': 'unresolved', 'source': source, 'cues': cues,
              'resource_ids': sorted(resources), 'unresolved': unresolved,
              'source_file': 'BinData/flowState/flowstate.json'}
    result['sha256'] = presentation_hash(result)
    return result
