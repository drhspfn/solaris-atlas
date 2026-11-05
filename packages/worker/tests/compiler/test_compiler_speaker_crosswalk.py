import json

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.entities import compile_entities


def test_compiler_emits_all_exact_head_variants_with_source_provenance(tmp_path):
    for relative, rows in {
        'role/roleinfo.json': [{'Id': 1212, 'RoleHeadIconCircle': '/jingran',
                                'FormationRoleCard': '/own'}],
        'speaker/speaker.json': [
            {'Id': 400049, 'HeadIconAsset': '/jingran', 'RolePileIconAsset': '/borrowed'},
            {'Id': 800545, 'HeadIconAsset': '/jingran', 'RolePileIconAsset': '/borrowed'},
        ],
    }.items():
        path = tmp_path / 'BinData' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    writer = Writer(tmp_path / 'out', 'fixture')
    subtitles = tmp_path / 'Textmaps/en/subtitle_text/SubtitleText.json'
    subtitles.parent.mkdir(parents=True)
    subtitles.write_text('[]')
    compile_entities(tmp_path, writer, {})
    writer.finish()
    edges = [json.loads(line) for line in (tmp_path / 'out/graphs/global.jsonl').read_text().splitlines()]
    crosswalk = [e for e in edges if e['type'] == 'references_character']
    assert {e['from'] for e in crosswalk} == {'speaker:400049', 'speaker:800545'}
    assert {e['to'] for e in crosswalk} == {'character:1212'}
    assert {e['raw_path'] for e in crosswalk} == {'$[0].HeadIconAsset', '$[1].HeadIconAsset'}
    assert all(e['relation'] == 'exact_join' and e['crosswalk']['matches'] for e in crosswalk)
