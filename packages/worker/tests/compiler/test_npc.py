import json

from wuwa_story.ingestion.npc_sources import normalize_npcs

from wuwa_story_worker.compiler.core import Writer
from wuwa_story_worker.compiler.npc import compile_npcs


def test_npc_discovery_overrides_and_evidence(tmp_path):
    blueprints = [{"Id": 11, "BlueprintType": "A", "EntityType": "Npc"},
                  {"Id": 12, "BlueprintType": "B", "EntityType": "Npc"},
                  {"Id": 13, "BlueprintType": "C", "EntityType": "Monster"}]
    templates = [
        {"Id": 101, "BlueprintType": "A", "ComponentsData": {
            "BaseInfoComponent": {"Category": {"MainType": "Npc"}, "TidName": "Entity_A"},
            "ModelComponent": {"ModelType": {"Type": "Npc", "NpcModel": {"Mesh": "/Game/A"}}}}},
        {"Id": 102, "BlueprintType": "B", "ComponentsData": json.dumps({
            "BaseInfoComponent": {"Category": {}, "TidName": "Entity_A"},
            "ModelComponent": {"ModelType": {"Type": "Npc"}}})},
        {"Id": 103, "BlueprintType": "C", "ComponentsData": {}}]
    override = {"BaseInfoComponent": {"TidName": ""},
                "ModelComponent": {"ModelType": {"NpcModel": {"Mesh": "/Game/Override"}}},
                "InteractComponent": {"actions": [
                    {"Name": "OpenSystemBoard", "Params": {
                        "SystemType": "Shop", "BoardId": 8}},
                    {"Name": "OpenSystemBoard", "Params": {"SystemType": "Other", "BoardId": 9}}],
                    "flow": {"FlowListName": "Test", "FlowId": 1, "StateId": 2}}}
    entities = [{"Id": 1, "BlueprintType": "A", "MapId": 2, "EntityId": 3,
                 "ComponentsData": override},
                {"Id": 2, "BlueprintType": "A", "ComponentsData": {
                    "BaseInfoComponent": {"Category": {"MainType": "Monster"}}}}]
    english = {"Entity_A": {"content": "Same name", "source": {"file": "text"}},
               "Speaker_7_Name": {"content": "Same name"}}
    args = (blueprints, templates, entities, english, [{"Id": 8}],
            [{"Id": 20, "ShopId": 8}], [], [], [{"Id": 7}])
    records = normalize_npcs(*args)
    assert records == normalize_npcs(*args)
    assert [record["id"] for record in records] == [
        "character:npc:11", "character:npc:12"]
    assert records[0]["shared_artwork_paths"] == []
    npc = records[0]["npc"]
    assert npc["instances"][0]["components"] == override
    assert npc["instances"][0]["name"] is None
    assert len(npc["instances"]) == 1
    assert npc["model"]["ModelType"]["NpcModel"]["Mesh"] == "/Game/A"
    assert npc["shop_ids"] == [8]
    assert npc["shops"][0]["shopfixed"] == [{"Id": 20, "ShopId": 8}]
    # Equal translated text is not identity evidence.
    assert npc["speakers"] == []
    assert npc["flow_references"][0]["flow"] == ["Test", 1, 2]

    for path, rows in (("blueprint/blueprintconfig.json", blueprints),
                       ("template/templateconfig.json", templates),
                       ("level_entity/levelentityconfig.json", entities),
                       ("shop/shopinfo.json", [{"Id": 8}]),
                       ("shop/shopfixed.json", [{"Id": 20, "ShopId": 8, "ItemId": 100, "ItemNum": 2}])):
        target = tmp_path / "BinData" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows), encoding="utf-8")
    writer = Writer(tmp_path / "out", "test")
    assert compile_npcs(tmp_path, writer, english) == {"character": 2}
    writer.finish()
    written = [json.loads(line) for line in (
        tmp_path / "out/entities/character.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["id"] for row in written] == [
        "character:npc:11", "character:npc:12"]
    edges = [json.loads(line) for line in (tmp_path / "out/graphs/global.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {(edge["type"], edge["to"]) for edge in edges} == {
        ("references_flow_state", "flow_state:Test_1_2"), ("references_item", "item:100")}
    offer = next(edge for edge in edges if edge["type"] == "references_item")
    assert json.loads(offer["basis"])["quantity"] == 2
    assert offer["source"] == "BinData/shop/shopfixed.json"


def test_explicit_name_key_does_not_prove_speaker_identity():
    rows = normalize_npcs(
        [{"Id": 1, "BlueprintType": "A", "EntityType": "Npc"}],
        [{"BlueprintType": "A", "ComponentsData": {"BaseInfoComponent": {
            "Category": {"MainType": "Npc"}, "TidName": "Speaker_7_Name"}}}],
        [], {}, speakers=[{"Id": 7}])
    assert rows[0]["npc"]["speakers"] == []


def test_model_preloads_use_effective_models_and_compile_optional_input(tmp_path):
    blueprint = [{"Id": 1, "BlueprintType": "A", "EntityType": "Npc"}]
    template = [{"BlueprintType": "A", "ComponentsData": {
        "BaseInfoComponent": {"Category": {"MainType": "Npc"}},
        "ModelComponent": {"ModelType": {"Type": "ModelId", "ModelId": 10}}}}]
    entities = [{"BlueprintType": "A", "ComponentsData": {"ModelComponent": {"ModelType": value}}}
                for value in ({"ModelId": 20}, {"Type": "Npc", "ModelId": 30},
                              {"ModelId": 999}, {"ModelId": True})]
    preloads = [{"Id": i, "Meshes": [f"/Game/{i}"], "_client_source": {"key": i}}
                for i in (10, 20, 30, 40)]
    records = normalize_npcs(blueprint, template, entities, {}, model_preloads=preloads)
    assert records[0]["npc"]["model_preloads"] == preloads[:2]
    assert normalize_npcs(blueprint, template, entities, {})[0]["npc"]["model_preloads"] == []

    for path, data in (("blueprint/blueprintconfig.json", blueprint),
                       ("template/templateconfig.json", template),
                       ("level_entity/levelentityconfig.json", entities),
                       ("model_config_preload/modelconfigpreload.json", preloads)):
        target = tmp_path / "BinData" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data), encoding="utf-8")
    writer = Writer(tmp_path / "out", "test")
    compile_npcs(tmp_path, writer, {})
    writer.finish()
    row = json.loads((tmp_path / "out/entities/character.jsonl").read_text(encoding="utf-8"))
    assert row["npc"]["model_preloads"] == preloads[:2]


def test_research_native_name_variants():
    # Actual keys and instance evidence from the native source research export.
    iris = "Entity_beccf0c91f56d418c84e5cb684eff4012e563863774a907f4e6c3f6e19f55fd3"
    zahira = "Entity_6e82016d7c1a3fa9e659cef99ad6494d3eb40f84a4e0c3195aba2c891f335463"
    rosemary = "Entity_edbfe812d2e62999d99ec5ed82828479492ec61b0a10fc3d5821b80d45c47d52"
    bps = [{"Id": i, "BlueprintType": str(i), "EntityType": "Npc"}
           for i in (15267, 17288, 17290)]
    templates = [{"BlueprintType": str(i), "ComponentsData": {"BaseInfoComponent": {
        "Category": {"MainType": "Npc"}, "TidName": key}}}
        for i, key in ((15267, rosemary), (17288, iris), (17290, iris))]
    entities = [{"BlueprintType": str(bp), "EntityId": eid, "ComponentsData": components}
                for bp, eid, components in (
                    (15267, 314900015, {}), (17288, 152700001, {}),
                    (17290, 169700043, {
                     "BaseInfoComponent": {"TidName": zahira}}),
                    (17290, 181700897, {}))]
    english = {key: {"content": text} for key, text in
               ((iris, "I.R.I.S."), (zahira, "Zahira"), (rosemary, "Rosemary"))}
    rows = normalize_npcs(bps, templates, entities, english)
    assert [row["name"]["en"] if row["name"] else None for row in rows] == [
        "Rosemary", "I.R.I.S.", None]
    assert {v["name"]["en"] for v in rows[2]["npc"]
            ["name_variants"]} == {"I.R.I.S.", "Zahira"}
    only_zahira = normalize_npcs(bps, templates, entities[:-1], english)
    assert only_zahira[2]["name"]["en"] == "Zahira"
