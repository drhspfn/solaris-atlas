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


async def _identity_localize(key):
    return {"content": key}


def test_role_kind_does_not_treat_creatures_and_temporary_roles_as_resonators():
    from wuwa_story.ingestion.character_identity import character_kind
    assert character_kind(1) == "playable"
    assert {character_kind(role) for role in (2, 4, 5)} == {"story"}
    assert character_kind(3) == character_kind(None) == "technical"


def test_temporary_rover_roles_join_the_identity_but_are_not_duplicate_selectable_forms():
    male = {"Id": 1501, "ElementId": 5, "Name": "Name_Rover", "RoleHeadIcon": "male-head", "FormationRoleCard": "male-card"}
    temporary = {**male, "Id": 5055, "RoleType": 2}
    stranger = {**temporary, "Id": 5015, "RoleHeadIcon": "different-person"}
    identities = main_role_identities([{"Id": 1501, "Gender": 1, "UnlockCondition": 0}], [male, temporary, stranger])
    assert identities[5055]["canonical_key"] == "character:1501"
    assert identities[5055]["variant_key"] == "character:1501"
    assert identities[5055]["selectable"] is False
    assert 5015 not in identities
