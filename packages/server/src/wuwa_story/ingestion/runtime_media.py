"""Explicit media references inside quest/level actions and travel transitions."""

from wuwa_story.ingestion.dungeon_context import walk


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
