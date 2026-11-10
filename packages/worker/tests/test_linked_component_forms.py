import pytest

from wuwa_story_worker.linked_component_forms import prepare_linked_components

FORM = {"id": "mesh-1"}


def recipe(identity, parent, *, name=None, kind="component"):
    return {"model": "Aki/Prop/" + identity, "blueprints": [], "component": {
        "id": identity, "name": name or identity, "kind": kind, "parent_component": parent,
        "bindings": [], "cues": [], "socket": None}}


def test_batch_resolves_siblings_and_chains_in_any_recipe_order():
    recipes = [recipe("child", "OtherCase1"), recipe("parent", "CharacterMesh0", name="OtherCase1"),
               recipe("sibling", "Mesh"), recipe("effect", None, kind="effect")]
    child, parent, sibling, effect = prepare_linked_components(FORM, recipes)
    assert child[1]["parent_form_id"] == parent[0]
    assert parent[1]["parent_form_id"] == sibling[1]["parent_form_id"] == effect[1]["parent_form_id"] == FORM["id"]
    assert recipes[0]["component"].get("parent_form_id") is None


@pytest.mark.parametrize("recipes", [
    [recipe("first", "second"), recipe("second", "first")],
    [recipe("self", "self")],
])
def test_batch_rejects_parent_cycles(recipes):
    with pytest.raises(ValueError, match="Cyclic"):
        prepare_linked_components(FORM, recipes)


@pytest.mark.parametrize("recipes", [
    [recipe("first", "unknown")],
    [recipe("first", None)],
    [recipe("first", "Mesh", name="same"), recipe("second", "Mesh", name="same")],
    [recipe("same", "Mesh"), recipe("same", "Mesh")],
])
def test_batch_rejects_missing_or_ambiguous_parents(recipes):
    with pytest.raises(ValueError):
        prepare_linked_components(FORM, recipes)


def test_component_budget_keeps_distinct_instances_and_rejects_overflow():
    recipes = [recipe(f"effect-{i}", "Mesh") for i in range(129)]
    assert len({identity for identity, _ in prepare_linked_components(FORM, recipes[:70])}) == 70
    assert len(prepare_linked_components(FORM, recipes[:128])) == 128
    with pytest.raises(ValueError, match="128"):
        prepare_linked_components(FORM, recipes)
