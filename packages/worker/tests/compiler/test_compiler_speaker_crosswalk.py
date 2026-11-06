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


def test_compiler_emits_mainrole_family_card_proofs_for_both_genders(tmp_path):
    from wuwa_story.ingestion.speaker_crosswalk import PERSONAL_PORTRAIT_BASIS

    for relative, rows in {
        'BinData/role/roleinfo.json': [
            {'Id': 1501, 'RoleType': 1, 'FormationRoleCard': '/male'},
            {'Id': 1502, 'RoleType': 1, 'FormationRoleCard': '/female'}],
        'BinData/main_role_change/mainroleconfig.json': [
            {'Id': 1501, 'Gender': 1, 'UnlockCondition': 0},
            {'Id': 1502, 'Gender': 0, 'UnlockCondition': 0}],
        'BinData/speaker/speaker.json': [
            {'Id': identity, 'RolePileIconAsset': '/male' if identity == 1450 else '/female'}
            for identity in (317, 750088, 1450, 300013)],
        'Textmaps/en/subtitle_text/SubtitleText.json': [],
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    writer = Writer(tmp_path / 'out', 'fixture')
    compile_entities(tmp_path, writer, {})
    writer.finish()
    edges = [json.loads(line) for line in (tmp_path / 'out/graphs/global.jsonl').read_text().splitlines()]
    crosswalk = [edge for edge in edges if edge['type'] == 'references_character']
    assert {edge['from'] for edge in crosswalk} == {
        'speaker:317', 'speaker:750088', 'speaker:1450', 'speaker:300013'}
    assert all(edge['to'] == 'character:1501' and edge['basis'].startswith(PERSONAL_PORTRAIT_BASIS)
               and edge['crosswalk']['method'] == 'main_role_config_exact_asset' for edge in crosswalk)


def test_compiler_passes_guest_rows_and_english_with_source_provenance(tmp_path):
    for relative, rows in {
        'BinData/role/roleinfo.json': [{'Id': 1402, 'RoleType': 1, 'Name': 'role_name'}],
        'BinData/speaker/speaker.json': [{'Id': 108}],
        'BinData/PlotGuest/plotguest.json': [{'GuestID': 1, 'Name': 'role_name', 'SpeakerID': [108]}],
        'Textmaps/en/subtitle_text/SubtitleText.json': [],
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
    writer = Writer(tmp_path / 'out', 'fixture')
    compile_entities(tmp_path, writer, {'role_name': {'content': 'Yangyang'},
                                       'Speaker_108_Name': {'content': 'Yangyang'}})
    writer.finish()
    edges = [json.loads(line) for line in (tmp_path / 'out/graphs/global.jsonl').read_text().splitlines()]
    guest = [edge for edge in edges if edge['type'] == 'references_character']
    assert len(guest) == 1
    assert (guest[0]['from'], guest[0]['to'], guest[0]['source'], guest[0]['raw_path']) == (
        'speaker:108', 'character:1402', 'BinData/PlotGuest/plotguest.json', '$[0].SpeakerID[0]')
    assert guest[0]['crosswalk']['method'] == 'exact_plot_guest'
