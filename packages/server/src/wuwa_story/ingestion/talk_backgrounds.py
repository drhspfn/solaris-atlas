"""Authored dialogue illustrations, including explicit Rover variants and clearing."""


def background_config(value):
    if not isinstance(value, dict):
        return None
    kind = value.get('Type')
    if kind == 'Clean':
        return {'kind': 'clear', 'images': []}
    fields = {'Image': [('ImageAsset', None)],
              'ImageByMcGender': [('ImageAssetMale', 'male'), ('ImageAssetFemale', 'female')]}
    if kind in fields:
        images = [{'engine_path': value[field], 'rover': rover} for field, rover in fields[kind]
                  if isinstance(value.get(field), str) and value[field].startswith('/Game/')]
        return {'kind': 'image', 'images': images} if images else None
    if kind == 'SpineImage' and type(value.get('Id')) is int:
        return {'kind': 'spine', 'resource_id': value['Id'], 'loop': value.get('IsLoop', False), 'images': []}
    return None


def background_changes(items):
    for index, item in enumerate(items if isinstance(items, list) else []):
        config = background_config(item.get('BackgroundConfig')) if isinstance(item, dict) else None
        if config is not None:
            yield index, config
