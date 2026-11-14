"""Inspect retained model-export workspaces without changing their contents.

The converted files are candidates, not proof they belong to the scene.
This report cannot establish resources that were never extracted.
"""

import argparse
import json
from pathlib import Path


def diagnose(root: Path) -> dict:
    components = []
    for workspace in sorted(root.iterdir()):
        cooked = workspace / 'source-json' / 'Client' / 'Content'
        converted = workspace / 'converted' / 'Client' / 'Content'
        if not cooked.is_dir():
            continue
        meshes = []
        errors = []
        for model in sorted(converted.rglob('*.uemodel')):
            relative = model.relative_to(converted).with_suffix('')
            source = cooked / relative.with_suffix('.json')
            entry = {'model': relative.as_posix(), 'metadata_found': source.is_file(), 'materials': []}
            if source.is_file():
                try:
                    rows = json.loads(source.read_text(encoding='utf-8-sig'))
                    for row in rows:
                        if row.get('Type') not in ('SkeletalMesh', 'StaticMesh'):
                            continue
                        for slot in row.get('SkeletalMaterials', row.get('StaticMaterials', [])):
                            reference = slot.get('Material') or slot.get('MaterialInterface') or {}
                            path = reference.get('ObjectPath')
                            if path:
                                entry['materials'].append(path)
                except (ValueError, OSError, AttributeError) as error:
                    errors.append({'stage': 'mesh_metadata', 'resource': str(relative), 'error': str(error)})
            meshes.append(entry)
        log = workspace / 'blender.log'
        if log.is_file():
            lines = log.read_text(encoding='utf-8', errors='replace').splitlines()
            for line in lines:
                if line.startswith(('ValueError:', 'RuntimeError:', 'FileNotFoundError:')):
                    errors.append({'stage': 'blender', 'error': line})
        components.append({'workspace': workspace.name, 'meshes': meshes, 'errors': errors})
    return {'format': 'solaris-model-workspace-diagnostic-v1',
            'limitations': ['Converted meshes are candidates, not confirmed scene relationships.',
                            'Unextracted resources and visual correctness are not checked.'],
            'components': components}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = diagnose(args.workspace)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"Inspected {len(report['components'])} workspaces; report: {args.output}")


if __name__ == '__main__':
    main()
