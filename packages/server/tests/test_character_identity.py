from wuwa_story.ingestion.character_identity import main_role_identities


def test_rover_identity_discovers_new_elements_from_main_role_config():
    roles = [{"Id": 1501, "ElementId": 5}, {"Id": 1502, "ElementId": 5},
             {"Id": 91001, "ElementId": 9}, {"Id": 91002, "ElementId": 9},
             {"Id": 5052, "ElementId": 4}]
    configs = [{"Id": 1501, "Gender": 1, "UnlockCondition": 0},
               {"Id": 1502, "Gender": 0, "UnlockCondition": 0},
               {"Id": 91001, "Gender": 1, "UnlockCondition": 42},
               {"Id": 91002, "Gender": 0, "UnlockCondition": 42}]
    identities = main_role_identities(configs, roles, [{"Id": 9, "Name": "Element_New"}])
    assert set(identities) == {1501, 1502, 91001, 91002}
    assert {row["canonical_key"] for row in identities.values()} == {"character:1501"}
    assert identities[91002]["gender"] == "female"
    assert identities[91002]["element_name_key"] == "Element_New"
    assert identities == main_role_identities(list(reversed(configs)), roles, [{"Id": 9, "Name": "Element_New"}])


def test_missing_default_does_not_guess_family_from_names_or_role_ids():
    assert main_role_identities([], [{"Id": 1501, "Name": "Rover"}]) == {}
    assert main_role_identities([{"Id": 1501, "Gender": 9, "UnlockCondition": 0}], [{"Id": 1501}]) == {}
