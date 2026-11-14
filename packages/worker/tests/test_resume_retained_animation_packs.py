import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("resume_packs", Path(__file__).parents[3] / "scripts/resume_retained_animation_packs.py")
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.plan = {"skeleton": "skel", "model_sha256": "model", "rig_signature": {
            "object": [1.0], "bones": [["bone", None, [1.0]]]}, "clips": [
                {"source": "/Game/a", "file": "a.seq", "sha256": "abc", "name": "A", "action": "A"}],
            "animations": [], "facial": {"clips": []}}
        self.recipe = "recipe"
        self.entry = {"id": "pack", "identities": {resume.clip_identity(self.plan["clips"][0]): "A"},
            "rig_signature": self.plan["rig_signature"], "rig_namespace": resume.fingerprint(["skel", self.recipe])}

    def test_reuses_complete_pack(self):
        resume.verify_reuse(self.plan, self.recipe, self.entry)

    def test_rejects_changed_inputs_and_populated_cache(self):
        changes = [("skeleton", "other"), ("model_sha256", "other"), ("rig_signature", None)]
        for key, value in changes:
            altered = copy.deepcopy(self.plan)
            altered[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                resume.inject(self.plan, self.recipe, self.entry, altered, {}, self.recipe)
        for key in ("source", "file", "sha256", "name"):
            altered = copy.deepcopy(self.plan)
            altered["clips"][0][key] = "changed"
            with self.subTest(key=key), self.assertRaises(ValueError):
                resume.inject(self.plan, self.recipe, self.entry, altered, {}, self.recipe)
        with self.assertRaises(ValueError):
            resume.inject(self.plan, self.recipe, self.entry, self.plan, {}, "changed")
        with self.assertRaises(ValueError):
            resume.inject(self.plan, self.recipe, self.entry, self.plan, {"existing": []}, self.recipe)

    def test_rejects_wrong_pack_identity(self):
        entry = copy.deepcopy(self.entry)
        entry["identities"] = {"wrong": "A"}
        with self.assertRaises(ValueError):
            resume.verify_reuse(self.plan, self.recipe, entry)

    def test_rejects_changed_manifest_or_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory) / "root" / "bundle"
            (bundle / "animation-packs").mkdir(parents=True)
            plan_path = bundle / "animation-plan.json"
            plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
            plan_hash = resume.sha256(plan_path)
            pack_hash = "a" * 64
            pack_id = pack_hash[:16]
            (bundle / "animation-packs" / (pack_id + ".glb")).write_bytes(b"pack")
            manifest_path = bundle / "animation-manifest.json"
            manifest = {"format": "solaris-animation-packs-v1", "packs": [
                {"id": pack_id, "key": "animation_pack_" + pack_id, "clips": ["A"]}]}
            with patch.dict(resume.CASES, {"test": ("root", plan_hash, pack_hash, 1)}):
                manifest_path.write_text(json.dumps({**manifest, "packs": []}), encoding="utf-8")
                with self.assertRaises(ValueError):
                    resume.verified_pack("test", Path(directory))
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                with patch.object(resume.cm, "glb_summary", return_value={
                    "sha256": "wrong", "animations": 1, "clips": [{"name": "A"}]}):
                    with self.assertRaises(ValueError):
                        resume.verified_pack("test", Path(directory))


if __name__ == "__main__":
    unittest.main()
