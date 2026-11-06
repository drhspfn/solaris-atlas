"""Regression tests for exact config joins and instance identity overrides."""

import importlib.util
import json
import sqlite3
import struct
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("npc_models", Path(__file__).parents[1] / "npc_research_models.py")
models = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(models)


def blob(fields):
    """Small valid FlatBuffer table, including an omitted trailing field."""
    count = max(fields) + 1
    vtable_size = 4 + count * 2
    pos = 4 + vtable_size
    out = bytearray(pos + 4 + count * 4)
    struct.pack_into("<I", out, 0, pos)
    struct.pack_into("<HH", out, 4, vtable_size, 4 + count * 4)
    struct.pack_into("<i", out, pos, pos - 4)
    for index, value in fields.items():
        at = pos + 4 + index * 4
        struct.pack_into("<H", out, 8 + index * 2, 4 + index * 4)
        if isinstance(value, str):
            encoded = value.encode("utf-8")
            struct.pack_into("<I", out, at, len(out) - at)
            out.extend(struct.pack("<I", len(encoded)) + encoded + b"\0")
        else:
            struct.pack_into("<i", out, at, value)
    return bytes(out)


class ModelResearchTests(unittest.TestCase):
    def test_flatbuffer_schema_and_missing_fields(self):
        row = models.Row(blob({0: 17288, 1: models.IRIS, 4: 0}))
        self.assertEqual(row.integer(0), 17288)
        self.assertEqual(row.text(1), models.IRIS)
        self.assertEqual(row.integer(4), 0)
        self.assertEqual(row.text(3), "")
        self.assertEqual(row.text(8), "")

    def test_exact_blueprint_shared_template_names_and_all_npc_discovery(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locales = root / "en"
            locales.mkdir()
            with closing(sqlite3.connect(locales / "lang_multi_text.db")) as db, db:
                db.execute("CREATE TABLE MultiText (Id TEXT, Content TEXT)")
                db.executemany("INSERT INTO MultiText VALUES (?,?)", [
                    ("Entity_shared", "Shared default"), ("Entity_zahira", "Zahira"),
                ])
            components = {"BaseInfoComponent": {"TidName": "Entity_shared", "Category": {"MainType": "Npc"}}}
            mesh = "/Game/Exact/NotNamedIris.NotNamedIris"
            iris_components = {**components, "ModelComponent": {"ModelType": {"NpcModel": {"Mesh": mesh}}}}
            with closing(sqlite3.connect(root / "db_template.db")) as db, db:
                db.execute("CREATE TABLE templateconfig (Id INT, BlueprintType TEXT, BinData BLOB)")
                for identifier, bp, value in [(1, models.IRIS, iris_components), (2, "exact-zahira", components),
                                               (3, "unlocalized-npc", {"BaseInfoComponent": {"Category": {"MainType": "Npc"}}})]:
                    db.execute("INSERT INTO templateconfig VALUES (?,?,?)", (
                        identifier, bp, blob({0: identifier, 1: bp, 2: "test", 3: json.dumps(value)})))
            with closing(sqlite3.connect(root / "db_blueprint.db")) as db, db:
                db.execute("CREATE TABLE blueprintconfig (Id INT, BlueprintType TEXT, BinData BLOB)")
                for identifier, bp in [(1, models.IRIS), (2, "exact-zahira"), (3, "unlocalized-npc")]:
                    db.execute("INSERT INTO blueprintconfig VALUES (?,?,?)", (identifier, bp, blob({0: identifier, 4: 0})))
            entities = [
                {"Id": 1, "MapId": 906, "EntityId": 152700001, "BlueprintType": models.IRIS, "ComponentsData": {}},
                {"Id": 2, "MapId": 906, "EntityId": 152700000, "BlueprintType": "exact-zahira",
                 "ComponentsData": {"BaseInfoComponent": {"TidName": "Entity_zahira"}}},
                {"Id": 3, "MapId": 906, "EntityId": 3, "BlueprintType": "exact-zahira",
                 "ComponentsData": {"BaseInfoComponent": {"TidName": ""}}},
                {"Id": 4, "MapId": 906, "EntityId": 4, "BlueprintType": "not-an-npc",
                 "ComponentsData": {"BaseInfoComponent": {"Category": {"MainType": "SceneItem"}}}},
                {"Id": 5, "MapId": 906, "EntityId": 5, "BlueprintType": "unlocalized-npc", "ComponentsData": {}},
                {"Id": 6, "MapId": 906, "EntityId": 6, "BlueprintType": "exact-zahira",
                 "ComponentsData": {"BaseInfoComponent": {"Category": {"MainType": "SceneItem"}}}},
            ]
            path = root / "entities.json"
            path.write_text(json.dumps(entities), encoding="utf-8")
            before = {p: p.read_bytes() for p in root.rglob("*.db")}
            result = models.research(root, path, locales, root / "metadata")
            chains = {c["blueprint_type"]: c for c in result["chains"]}
            iris = chains[models.IRIS]
            self.assertEqual(iris["models"][0]["config"]["ModelType"]["NpcModel"]["Mesh"], mesh)
            self.assertFalse(iris["mesh_verification"][0]["skeletal_mesh_export_verified"])
            self.assertEqual(iris["entities"][0]["identity"]["localized_name"], "Shared default")
            zahira = chains["exact-zahira"]["entities"]
            self.assertEqual(zahira[0]["identity"]["localized_name"], "Zahira")
            self.assertEqual(zahira[0]["identity"]["source"], "instance")
            self.assertIsNone(zahira[1]["identity"]["localized_name"])
            self.assertEqual(zahira[1]["identity"]["key"], "")
            all_result = models.research(root, path, locales, root / "metadata", all_npcs=True)
            all_chains = {c["blueprint_type"]: c for c in all_result["chains"]}
            self.assertEqual(set(all_chains), {models.IRIS, "exact-zahira", "unlocalized-npc"})
            self.assertTrue(all_chains["unlocalized-npc"]["unresolved"])
            self.assertEqual(len(all_chains["exact-zahira"]["entities"]), 2)
            self.assertEqual(before, {p: p.read_bytes() for p in root.rglob("*.db")})

    def test_missing_database_is_not_created(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            with self.assertRaises(sqlite3.OperationalError):
                models.open_db(path)
            self.assertFalse(path.exists())

    def test_empty_category_requires_validated_npc_template(self):
        template = {"ModelComponent": {"ModelType": {"Type": "Npc"}}}
        self.assertTrue(models.is_npc({"Category": {}}, template, "Npc"))
        self.assertFalse(models.is_npc({"Category": {}}, template, "SceneItem"))
        self.assertFalse(models.is_npc({"Category": {}}, {}, "Npc"))
        self.assertFalse(models.is_npc({"Category": {"MainType": "SceneItem"}}, template, "Npc"))


if __name__ == "__main__":
    unittest.main()
