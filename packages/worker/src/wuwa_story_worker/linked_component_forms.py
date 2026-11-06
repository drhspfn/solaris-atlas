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


def prepare_linked_components(form, recipes):
    """Return (form ID, relation) pairs in recipe order with verified scene parents."""
    if len(recipes) > 32:
        raise ValueError("Model scene exceeds 32 authored components")
    prepared = [prepare_linked_component(form, recipe) for recipe in recipes]
    aliases = {}
    for identity, relation in prepared:
        for name in (relation.get("id"), relation.get("name")):
            if name is None:
                continue
            if not isinstance(name, str) or not name or name in {"CharacterMesh0", "Mesh"}:
                raise ValueError("Invalid authored component identity")
            if name in aliases and aliases[name] != identity:
                raise ValueError("Ambiguous authored component identity")
            aliases[name] = identity
    relations = {}
    for identity, relation in prepared:
        if identity in relations:
            raise ValueError("Duplicate authored component in model scene")
        parent = relation.get("parent_component")
        if parent in ("CharacterMesh0", "Mesh") or parent is None and relation.get("kind") == "effect":
            relation["parent_form_id"] = form["id"]
        elif isinstance(parent, str) and parent in aliases:
            relation["parent_form_id"] = aliases[parent]
        else:
            raise ValueError("Unknown authored component parent")
        relations[identity] = relation
    for identity in relations:
        seen, current = set(), identity
        while current in relations:
            if current in seen:
                raise ValueError("Cyclic authored component parents")
            seen.add(current)
            current = relations[current]["parent_form_id"]
    return prepared
