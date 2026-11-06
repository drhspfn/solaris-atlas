"""Resolve authored modular NPC setup assets, without choosing nearby meshes."""

from wuwa_story_worker.model_sources import exported_package


def blueprint_sources(rows, package):
    """Follow the CDO's explicit Mesh component, or its authored Blueprint parent."""
    components = set()
    for row in rows:
        if str(row.get('Name', '')).startswith('Default__'):
            reference = (row.get('Properties') or {}).get('Mesh') or {}
            name = reference.get('ObjectName', '')
            if name.startswith("SkeletalMeshComponent'") and ':' in name:
                components.add(name.rsplit(':', 1)[-1].removesuffix("'"))
    meshes, blueprints = [], set()
    for row in rows:
        if row.get('Type') != 'SkeletalMeshComponent' or row.get('Name') not in components:
            continue
        value = (row.get('Properties') or {}).get('SkeletalMesh')
        anim = (row.get('Properties') or {}).get('AnimClass') or {}
        if str(anim.get('ObjectName', '')).startswith("AnimBlueprintGeneratedClass'"):
            blueprints.add(exported_package(anim.get('ObjectPath', '')))
        if value:
            if not isinstance(value, dict) or not str(value.get('ObjectName', '')).startswith("SkeletalMesh'"):
                raise ValueError('NPC Blueprint has an unsupported primary mesh reference')
            meshes.append(exported_package(value.get('ObjectPath', '')))
    if len(set(meshes)) > 1:
        raise ValueError('NPC Blueprint has ambiguous primary skeletal meshes')
    if meshes:
        return {'model': meshes[0], **({'blueprints': sorted(blueprints)} if blueprints else {})}
    parents = {exported_package(row['SuperStruct']['ObjectPath']) for row in rows
               if row.get('Type') == 'BlueprintGeneratedClass'
               and str((row.get('SuperStruct') or {}).get('ObjectPath', '')).startswith('/Game/')}
    if len(parents) == 1:
        return {'parent': next(iter(parents))}
    raise ValueError(f'NPC Blueprint has no authored primary skeletal mesh: {package}')


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
