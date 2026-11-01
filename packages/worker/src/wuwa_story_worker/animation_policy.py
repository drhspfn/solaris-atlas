"""Conservative source eligibility; conversion success is not render validation."""

import json


def assess_animation_sources(assets: dict, missing: list, roots: list[str]) -> dict:
    types = set()
    skeletons = set()
    atlases = set()
    direct_rigs = set()
    diagnostics = []
    for source, asset in assets.items():
        if asset.get('decode_failed'):
            diagnostics.append({'code': 'decode_failed', 'source': source})
            continue
        for file in asset['decoded'].rglob('*.json'):
            if file.name == 'references.json':
                continue
            try:
                value = json.loads(file.read_text(encoding='utf-8-sig'))
            except (OSError, ValueError):
                diagnostics.append({'code': 'invalid_decoded_source', 'source': source})
                continue
            for export in value if isinstance(value, list) else []:
                if not isinstance(export, dict):
                    continue
                kind = export.get('Type')
                types.add(kind)
                if kind == 'SpineSkeletonDataAsset':
                    skeletons.add(source)
                    if source in roots:
                        direct_rigs.add(source)
                elif kind == 'SpineAtlasAsset':
                    atlases.add(source)
    if missing:
        diagnostics.append({'code': 'missing_dependencies', 'count': len(missing)})
    if not skeletons or not atlases:
        diagnostics.append({'code': 'no_complete_spine'})
    elif len(skeletons) != 1:
        diagnostics.append({'code': 'composition_controls_required', 'rigs': len(skeletons)})
    if any(kind in types for kind in ('PrefabAsset', 'LevelSequence', 'SequenceData', 'NiagaraSystem', 'ParticleSystem', 'MediaPlayer')):
        diagnostics.append({'code': 'ui_composition_not_supported'})
    if types & {'Material', 'MaterialInstanceConstant'}:
        diagnostics.append({'code': 'material_semantics_unverified'})
    if skeletons and not direct_rigs:
        diagnostics.append({'code': 'no_explicit_rig_root'})
    allowed = bool(skeletons and atlases) and not diagnostics
    return {'schema_version': 1, 'display': 'static',
            'state': 'awaiting_render_validation' if allowed else 'static_only',
            'conversion_allowed': allowed,
            'reasons': diagnostics or [{'code': 'render_validation_required'}]}
