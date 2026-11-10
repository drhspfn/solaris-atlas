import hashlib
import json

import pytest

from wuwa_story_worker import character_models as worker


@pytest.mark.asyncio
async def test_skin_build_exports_only_delta_and_keeps_own_rig_and_physics(tmp_path, monkeypatch):
    tool = tmp_path / "tool"
    tool.write_bytes(b"pinned-tool")
    plugin = tmp_path / "plugin"
    (plugin / "io_scene_ueformat").mkdir(parents=True)
    monkeypatch.setenv("WUWA_UEFORMAT_PLUGIN", str(plugin))
    monkeypatch.setattr(worker, "tool_path", lambda *_: tool)
    monkeypatch.setattr(worker, "model_progress", _noop)
    models = [f"Aki/Character/Role/FemaleM/Test/{name}/Model/{name}" for name in ("Base", "Skin")]
    forms = [{"id": f"mesh-{i + 1}", "label": name, "order": i, "model": model,
              "blueprints": [f"Aki/Physics/{name}"], "source": {"table": "roleinfo" if i == 0 else "roleskin",
                  "id": 1 if i == 0 else 2, **({"role_id": 1, "skin_id": 2} if i else {})}}
             for i, (name, model) in enumerate(zip(("Base", "Skin"), models, strict=True))]
    extracted, exported = [], []

    async def extract(root, sources, *args, **kwargs):
        extracted.append(sources["model"])
        converted, cooked = root / "converted", root / "source-json"
        for form in forms:
            path = converted / "Client/Content" / (form["model"] + ".uemodel")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(form["label"])
            source = cooked / "Client/Content" / (form["model"] + ".json")
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text(json.dumps([{"Type": "SkeletalMesh", "Name": form["label"], "Properties": {
                "Skeleton": {"ObjectPath": "/Game/Aki/Shared/Rig.Rig"}}}]))
            blueprint = cooked / "Client/Content" / (form["blueprints"][0] + ".json")
            blueprint.parent.mkdir(parents=True, exist_ok=True)
            blueprint.write_text(json.dumps([{"skin": form["label"]}]))
        return converted / "Client/Content" / (sources["model"] + ".uemodel"), converted, cooked, {"files": []}

    async def blender(args, root, *unused, **kwargs):
        def value(flag):
            return args[args.index(flag) + 1]
        output, plan_file = value("--output"), value("--animation-plan")
        if "--rig-only" in args:
            skin = value("--model").stem == "Skin"
            clips = [{"name": f"Clip{i}", "action": f"Clip{i}", "source": f"Aki/Common/{i}.json",
                      "sha256": hashlib.sha256(str(i).encode()).hexdigest()} for i in range(420 if skin else 400)]
            plan = {"skeleton": "aki/shared/rig", "rig_fingerprint": "identical-rest-pose",
                    "model_sha256": worker.sha256(value("--model")), "clips": clips,
                    "animations": [{**clip, "included_in_glb": True} for clip in clips],
                    "facial": {"format": "solaris-facial-v1", "clips": []},
                    "animation_coverage": {"playable_clips": len(clips), "missing_exports": 0}}
            plan_file.write_text(json.dumps(plan))
            (output / "preview.glb").write_text("skin" if skin else "base")
            (output / "character.glb").write_text("skin-full" if skin else "base-full")
        else:
            plan = json.loads(plan_file.read_text())
            start, size = int(value("--animation-batch")), int(value("--animation-batch-size"))
            clips = plan["clips"][start * size:(start + 1) * size]
            exported.extend(clip["name"] for clip in clips)
            part = output / "animation-parts" / f"part-{start:04d}.glb"
            part.parent.mkdir(exist_ok=True)
            part.write_text(json.dumps([{"name": clip["name"]} for clip in clips]))

    def summary(path, allow_animations=False):
        result = {"bytes": path.stat().st_size, "bones": 10, "sha256": worker.sha256(path)}
        if allow_animations:
            clips = json.loads(path.read_text())
            result.update(animations=len(clips), clips=clips)
        return result

    def merge(parts, destination, **kwargs):
        destination.write_text(json.dumps([clip for part in parts for clip in json.loads(part.read_text())]))

    monkeypatch.setattr(worker, "extract_model", extract)
    monkeypatch.setattr(worker, "run_blender", blender)
    monkeypatch.setattr(worker, "glb_summary", summary)
    monkeypatch.setattr(worker, "merge_animation_glbs", merge)
    monkeypatch.setattr(worker, "spring_profile", lambda rows, *a, **k: {"groups": rows})
    bundles = await worker.build_scene_bundles(tmp_path, forms, local_client={})
    assert extracted == [models[0]]
    assert len(exported) == len(set(exported)) == 420
    a, b = bundles
    assert [len(p["clips"]) for p in a[3]["animation_packs"]] == [400]
    assert [len(p["clips"]) for p in b[3]["animation_packs"]] == [400, 20]
    assert a[3]["animation_packs"][0]["id"] == b[3]["animation_packs"][0]["id"]
    assert (a[1] / "character.glb").read_bytes() != (b[1] / "character.glb").read_bytes()
    assert json.loads((a[1] / "physics.json").read_text())["groups"] == [{"skin": "Base"}]
    assert json.loads((b[1] / "physics.json").read_text())["groups"] == [{"skin": "Skin"}]


async def _noop(*args, **kwargs):
    pass
