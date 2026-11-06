"""Explicit media references inside quest/level actions and travel transitions."""

import json

from wuwa_story.ingestion.dungeon_context import decoded, walk


def runtime_media(data, prefix='$', *, recursive=True):
    for path, value in walk(data, prefix) if recursive else [(prefix, data)]:
        name, params = value.get('Name'), value.get('Params')
        if name == 'PlayMovie' and isinstance(params, dict):
            video = params.get('VideoName')
            if isinstance(video, str) and video:
                yield {'kind': 'cutscene', 'reference': f'cutscene:{video}',
                       'raw_path': path + '.Params.VideoName', 'basis': 'PlayMovie.VideoName = CgName'}
        elif name == 'PlaySequenceData' and isinstance(params, dict):
            asset = params.get('Path')
            if isinstance(asset, str) and asset.startswith('/Game/'):
                yield {'kind': 'sequence', 'reference': 'asset:ue:' + asset,
                       'raw_path': path + '.Params.Path', 'basis': 'PlaySequenceData.Params.Path'}
        elif name == 'ShowTalk' and isinstance(params, dict):
            asset = params.get('SequenceDataAsset')
            if isinstance(asset, str) and asset.startswith('/Game/'):
                # SequenceNames select entries in the DA, not movie/package names.
                ref = {'kind': 'sequence', 'reference': 'asset:ue:' + asset,
                       'raw_path': path + '.Params.SequenceDataAsset',
                       'basis': 'ShowTalk.Params.SequenceDataAsset'}
                names = params.get('SequenceNames')
                if isinstance(names, list) and all(isinstance(value, str) and value for value in names):
                    ref['sequence_names'] = list(names)
                    ref['sequence_names_raw_path'] = path + \
                        '.Params.SequenceNames'
                yield ref
        if value.get('Type') == 'PlayMp4':
            video = value.get('Mp4Path')
            if isinstance(video, str) and video:
                yield {'kind': 'cutscene', 'reference': f'cutscene:{video}',
                       'raw_path': path + '.Mp4Path', 'basis': 'PlayMp4.Mp4Path = CgName',
                       'transition': True}


def quest_runtime_links(rows, available):
    for index, row in rows:
        key = row.get('Key', '')
        parts = key.split('_')
        if len(parts) != 2 or not all(part.isdecimal() for part in parts):
            continue
        for ref in runtime_media(row.get('Data'), f'$[{index}].Data'):
            if ref['reference'] not in available or ref['kind'] != 'cutscene':
                continue
            yield {'from': f'quest_node:{parts[0]}:{parts[1]}', 'to': ref['reference'],
                   'type': 'plays_cutscene', 'relation': 'explicit_reference',
                   'source': 'BinData/QuestNodeData/questnodedata.json',
                   'raw_path': ref['raw_path'], 'basis': ref['basis']}


def action_movie_links(owner, action, path, video_names):
    """Normalize movie references onto their owning action; nesting is not execution proof."""
    for ref in runtime_media(action, path):
        if ref['kind'] != 'cutscene':
            continue
        video = ref['reference'].removeprefix('cutscene:')
        extra = ({'nested_context': True, 'conditional': True, 'runtime_order_confirmed': False,
                  'owning_action_raw_path': path,
                  'context_note': 'Nested movie reference; branch execution is unconfirmed'}
                 if ref['raw_path'] != f'{path}.Params.VideoName' else {})
        basis = 'VideoName = CgName'
        if video not in video_names:
            if video.strip() not in video_names or sum(
                    candidate.strip() == video.strip() for candidate in video_names
                    if isinstance(candidate, str)) != 1:
                continue
            extra.update(raw_value=video, normalized_value=video.strip(
            ), resolution='unique_whitespace_normalization')
            video = video.strip()
            basis = 'unique whitespace-normalized VideoName = CgName'
        yield {'from': owner, 'to': f'cutscene:{video}', 'type': 'plays_cutscene',
               'relation': 'explicit_reference', 'source': 'BinData/flowState/flowstate.json',
               'raw_path': ref['raw_path'],
               'basis': json.dumps({'basis': basis, **extra}, sort_keys=True) if extra.get('nested_context') else basis,
               **extra}


def flow_movie_links(rows, video_names):
    for index, row in rows:
        key, actions = row.get('StateKey'), decoded(row.get('Actions'))
        if not isinstance(key, str) or not key or not isinstance(actions, list):
            continue
        for position, action in enumerate(actions):
            if isinstance(action, dict):
                yield from action_movie_links(f'action:{key}:{position}', action,
                                              f'$[{index}].Actions<json>[{position}]', video_names)
