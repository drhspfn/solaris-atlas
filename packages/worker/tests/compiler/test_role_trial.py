import json

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.entities import compile_entities


def test_trial_presentation_belongs_to_exact_character(tmp_path):
    base = tmp_path / "source" / "BinData"
    (base / "role").mkdir(parents=True)
    (base / "RoleTrial").mkdir()
    subtitles = tmp_path / "source" / "Textmaps" / "en" / "subtitle_text" / "SubtitleText.json"
    subtitles.parent.mkdir(parents=True)
    subtitles.write_text("[]")
    (base / "role" / "roleinfo.json").write_text(json.dumps([{"Id": 17}, {"Id": 18}]))
    (base / "RoleTrial" / "roletrialroleconfig.json").write_text(json.dumps([
        {"RoleId": 17, "FormationSpineSkeletonData": "/Game/Chixia.Chixia",
         "FormationSpineAtlas": "/Game/ChixiaAtlas.ChixiaAtlas", "SpineParam": [1, 2, 3]},
        {"RoleId": 18, "FormationSpineAtlas": "/Game/A"},
        {"RoleId": 18, "FormationSpineAtlas": "/Game/B"},
    ]))
    (base / "RoleTrial" / "roletrialinfo.json").write_text(json.dumps([
        {"Id": 9001, "RoleId": 17, "TrialRoleId": 1701, "InstanceId": 12, "GachaId": 23},
        {"Id": 9002, "RoleId": 99, "TrialRoleId": 9901},
    ]))
    out = tmp_path / "out"
    writer = Writer(out, "3.7.0")
    compile_entities(tmp_path / "source", writer, {})
    writer.finish()
    characters = [json.loads(line) for line in (out / "entities" / "character.jsonl").read_text().splitlines()]
    presentation = characters[0]["trial_presentation"]
    assert presentation["role_id"] == 17
    assert presentation["atlas"] == "/Game/ChixiaAtlas.ChixiaAtlas"
    assert presentation["skeleton"] == "/Game/Chixia.Chixia"
    assert presentation["spine_param"] == [1, 2, 3]
    assert presentation["source"]["file"] == "BinData/RoleTrial/roletrialroleconfig.json"
    assert characters[0]["trial_entries"] == [{
        "source": {"file": "BinData/RoleTrial/roletrialinfo.json", "row": 0},
        "id": 9001, "trial_role_id": 1701, "instance_id": 12, "gacha_id": 23,
    }]
    assert "trial_presentation" not in characters[1]
    assert "trial_entries" not in characters[1]
