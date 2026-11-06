"""Resolve authored modular NPC setup assets, without choosing nearby meshes."""

from wuwa_story_worker.model_sources import exported_package


def setup_sources(rows, package):
    matches = [row for row in rows if row.get('Name') == package.rsplit('/', 1)[-1]
               and row.get('Type') == 'PD_NpcSetupData_C']
    if len(matches) != 1:
        raise ValueError('NPC setup asset is missing or ambiguous')
    properties = matches[0].get('Properties') or {}

    def reference(field, kind):
        value = properties.get(field)
        if value is None:
            return None
        if not isinstance(value, dict) or not str(value.get('ObjectName', '')).startswith(kind + "'"):
            raise ValueError(f'Unsupported NPC setup reference: {field}')
        return exported_package(value.get('ObjectPath', ''))

    master = reference('Skel_Main', 'SkeletalMesh')
    parts = {field: reference(field, 'SkeletalMesh') for field in
             ('Skel_Body', 'Skel_Face', 'Skel_Hair')}
    # Unknown visible parts must not silently disappear from the composition.
    unknown = [key for key, value in properties.items() if key.startswith('Skel_')
               and key not in {'Skel_Main', *parts} and isinstance(value, dict)
               and str(value.get('ObjectName', '')).startswith("SkeletalMesh'")]
    if unknown:
        raise ValueError(f'Unsupported NPC setup mesh fields: {sorted(unknown)}')
    if not master:
        raise ValueError('NPC setup has no master skeletal mesh')
    meshes = list(dict.fromkeys(value for value in parts.values() if value))
    materials = {}
    for field, material_field in (('Skel_Body', 'Skel_Body_Mat'),
                                  ('Skel_Face', 'Face_Mat'), ('Skel_Hair', 'Hair_Mat')):
        material = reference(material_field, 'MaterialInstanceConstant')
        if material and parts[field]:
            materials[parts[field]] = {'0': material}
    return {'model': master, 'extra_models': meshes, 'master_pose': bool(meshes),
            'hide_primary_geometry': bool(meshes), 'part_material_overrides': materials,
            'data_asset': package}
