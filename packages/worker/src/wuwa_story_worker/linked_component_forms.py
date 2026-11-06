"""Prepare deterministic published component identities from trusted discovery."""

import hashlib


def prepare_linked_component(form, recipe):
    relation = dict(recipe["component"])
    relation["baked"] = bool(not recipe.get("blueprints") and not relation.get("bindings")
                             and not relation.get("cues")
                             and relation.get("parent_component") == "CharacterMesh0"
                             and relation.get("socket"))
    identity = "component-" + hashlib.sha256(
        (form["id"] + "|" + relation["id"]).encode()).hexdigest()[:16]
    relation["parent_form_id"] = form["id"]
    return identity, relation
