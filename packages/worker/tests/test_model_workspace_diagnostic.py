import importlib.util
import json
from pathlib import Path


def test_diagnostic_reports_candidate_material_and_blender_failure(tmp_path):
    script = Path(__file__).resolve().parents[3] / 'scripts' / 'diagnose-model-workspace.py'
    spec = importlib.util.spec_from_file_location('model_workspace_diagnostic', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    component = tmp_path / 'component-example'
    cooked = component / 'source-json/Client/Content/Aki'
    converted = component / 'converted/Client/Content/Aki'
    cooked.mkdir(parents=True)
    converted.mkdir(parents=True)
    (converted / 'Example.uemodel').touch()
    (cooked / 'Example.json').write_text(json.dumps([{'Type': 'SkeletalMesh',
        'SkeletalMaterials': [{'MaterialInterface': {'ObjectPath': '/Game/Aki/Material'}}]}]))
    (component / 'blender.log').write_text('ValueError: missing animation\n')
    report = module.diagnose(tmp_path)
    assert report['components'][0]['meshes'][0]['materials'] == ['/Game/Aki/Material']
    assert report['components'][0]['errors'][0]['error'] == 'ValueError: missing animation'
