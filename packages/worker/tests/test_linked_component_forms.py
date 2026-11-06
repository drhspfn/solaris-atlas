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
