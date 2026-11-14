"""Resume the two verified 2026-11-10 local animation packs; default is check-only."""
import argparse
import asyncio
import copy
import json
import os
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from wuwa_story.ingestion.model_archives import validate_manifest
from wuwa_story_worker import character_models as cm, model_cli
from wuwa_story_worker.media_provenance import sha256
from wuwa_story_worker.model_animation_packs import clip_identity, compatible_rigs, fingerprint
from wuwa_story_worker.model_forms import resolve_recipe_forms

CASES = {
    "hsin": ("mesh-100071", "36712d2cf01a14f58152840903ea303903c313c168df858b0356970b8b35f0d6", "d70ce1f5a0dea717f9d90969747b01d1ffdb3d8fdaa1407eda21a3252518360e", 506),
    "suisui": ("mesh-100070", "29abbb6541660b6fb58472471567b5395c47c62d9c228e2a7f0e60182cb2ea74", "9e9853d2484cafbc1d2db5ce2e505b02caa2079dde1f472753aa7a565abea029", 356),
}
TOOLS = ["7143a9f6e3b8c2e4f5c3499c79efc6687c135435151b9edc75a863c1b0f55cc4", "7421ab1c52a25df4b5dbb0f2ee3f008d9f71f4bb437ff69a1d0af78dfec50323", "b076a8f681247be0ff1131167f8325301b9f74ac7e4a57e3d08fe2237067492e", "0a5717717de1a33077c97155e649c5949141c2f0acb91b40ce89483507fa3b41"]


@contextmanager
def retained_workspace(*, prefix, dir):
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=dir))
    print(json.dumps({"stage": "workspace_created", "workspace": str(path)}), flush=True)
    try:
        yield str(path)
    except BaseException:
        print(json.dumps({"stage": "failed_workspace_preserved", "workspace": str(path)}), flush=True)
        raise
    else:
        shutil.rmtree(path)


def verified_pack(name, retained):
    form, plan_hash, pack_hash, count = CASES[name]
    bundle = retained / form / "bundle"
    plan_path = bundle / "animation-plan.json"
    if sha256(plan_path) != plan_hash:
        raise ValueError("Retained animation plan hash mismatch")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "animation-manifest.json").read_text(encoding="utf-8"))
    clips = plan["clips"]
    names = [c["name"] for c in clips]
    identities = {clip_identity(c): c["name"] for c in clips}
    pack_id = pack_hash[:16]
    if (len(clips) != count or len(identities) != count or len(set(names)) != count
            or not plan.get("skeleton") or not plan.get("model_sha256") or not plan.get("rig_signature")
            or manifest != {"format": "solaris-animation-packs-v1", "packs": [
                {"id": pack_id, "key": "animation_pack_" + pack_id, "clips": names}]}):
        raise ValueError("Retained plan or manifest is inconsistent")
    pack = bundle / "animation-packs" / (pack_id + ".glb")
    summary = cm.glb_summary(pack, allow_animations=True)
    if (summary["sha256"] != pack_hash or summary["animations"] != count
            or sorted(c["name"] for c in summary["clips"]) != sorted(names)):
        raise ValueError("Retained animation pack is corrupt or inconsistent")
    paths = [cm.tool_path("WUWA_TEXTURE_CONVERTER_PATH"), cm.tool_path("WUWA_BLENDER_PATH"),
             Path(cm.__file__).with_name("model_bundle.py"), Path(cm.__file__).with_name("model_animation_merge.py")]
    if [sha256(p) for p in paths] != TOOLS:
        raise ValueError("Animation tools or recipe differ from the retained build")
    if (sha256(Path(cm.__file__)) != "5fec73cbc42060370df435c35bb34ecf749aae25ca7ad1acf614d2760e0c6fc5"
            or sha256(Path(cm.__file__).with_name("model_animation_packs.py")) != "78151e48fb4f3f24a31d1350dcbd0e167d9feb72f31cb8fc8a94c6ee9ad3aac2"):
        raise ValueError("Importer or animation cache implementation differs from retained build")
    recipe = fingerprint(TOOLS)
    return plan, recipe, {"id": pack_id, "path": pack, "summary": summary,
                         "identities": identities, "rig_signature": plan["rig_signature"],
                         "rig_namespace": fingerprint([plan["skeleton"], recipe])}


def inject(saved, recipe, entry, current, cache, current_recipe):
    if (current_recipe != recipe or current.get("skeleton") != saved["skeleton"]
            or current.get("model_sha256") != saved["model_sha256"]
            or not compatible_rigs(saved["rig_signature"], current.get("rig_signature"))
            or [(clip_identity(c), c["name"]) for c in current["clips"]]
               != [(clip_identity(c), c["name"]) for c in saved["clips"]]):
        raise ValueError("Fresh model, rig, recipe, or animation identities differ from retained plan")
    if cache:
        raise ValueError("Expected an empty root animation cache")
    key = fingerprint([saved["skeleton"], saved.get("rig_fingerprint", saved["model_sha256"]), recipe])
    cache[key] = [entry]


def verify_reuse(saved, recipe, entry):
    current, cache = copy.deepcopy(saved), {}
    inject(saved, recipe, entry, current, cache, recipe)
    _, reused, unseen = cm.prepare_packs(current, cache, recipe)
    if len(reused) != 1 or len(reused[0]["clips"]) != len(saved["clips"]) or unseen or current["clips"]:
        raise ValueError("Animation pack was not fully reused")


async def resume(args, saved, recipe, entry):
    form_id = CASES[args.character][0]
    forms = resolve_recipe_forms(json.loads(args.recipe.read_text(encoding="utf-8-sig")))
    if not forms or forms[0]["id"] != form_id:
        raise ValueError("Recipe does not start with the expected root form")
    if args.output.resolve().is_relative_to(args.retained.resolve()):
        raise ValueError("Output must be outside the retained workspace")
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Separate scratch and private pack copy: retained data is never modified or hard-linked.
    with retained_workspace(prefix=".verified-resume-", dir=args.output.parent) as scratch:
        scratch = Path(scratch)
        pack = scratch / "retained.glb"
        shutil.copyfile(entry["path"], pack)
        if sha256(pack) != CASES[args.character][2]:
            raise ValueError("Copied animation pack hash mismatch")
        entry = {**entry, "path": pack}
        original_bundle, original_prepare = cm.build_bundle, cm.prepare_packs
        active = injected = False

        def prepare(current, cache, current_recipe):
            nonlocal injected
            if active:
                if injected:
                    raise ValueError("Unexpected repeated root pack injection")
                inject(saved, recipe, entry, current, cache, current_recipe)
                injected = True
            result = original_prepare(current, cache, current_recipe)
            if active and (len(result[1]) != 1 or len(result[1][0]["clips"]) != len(saved["clips"]) or result[2] or current["clips"]):
                raise ValueError("Fresh root did not reuse the complete retained pack")
            return result

        async def bundle(root, sources, include_animations=True, **options):
            nonlocal active
            if sources.get("id") != form_id or sources.get("component"):
                return await original_bundle(root, sources, include_animations=include_animations, **options)
            if active or injected or not include_animations:
                raise ValueError("Unexpected root invocation")
            active = True
            try:
                return await original_bundle(root, sources, include_animations=include_animations, **options)
            finally:
                active = False

        cm.prepare_packs, cm.build_bundle = prepare, bundle
        archive = scratch / "archive.zip"
        previous_tempfile = model_cli.tempfile
        model_cli.tempfile = SimpleNamespace(TemporaryDirectory=retained_workspace)
        try:
            await model_cli.build(SimpleNamespace(recipe=args.recipe, game_dir=args.game_dir, keys_file=args.keys_file,
                asset_version=args.asset_version, tier="hd", output=archive, workers=1, without_animations=False,
                fmodel=None, converter=None, blender=None, ueformat_plugin=None))
        finally:
            cm.prepare_packs, cm.build_bundle = original_prepare, original_bundle
            model_cli.tempfile = previous_tempfile
        if not injected:
            raise ValueError("Root animation pack was not injected")
        with zipfile.ZipFile(archive) as z:
            validate_manifest(json.loads(z.read("manifest.json")))
            if z.testzip() is not None:
                raise ValueError("Archive CRC check failed")
        # Atomic no-overwrite publication on the same filesystem.
        os.link(archive, args.output)
    print(json.dumps({"stage": "archive_ready", "archive": str(args.output)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("character", choices=CASES)
    parser.add_argument("--retained", type=Path, required=True, help="read-only retained workspace")
    parser.add_argument("--run", action="store_true", help="build a new archive after checks")
    parser.add_argument("--recipe", type=Path)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--keys-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--asset-version", default="3.7.2")
    args = parser.parse_args()
    if args.run and not all((args.recipe, args.game_dir, args.keys_file, args.output)):
        parser.error("--run requires --recipe, --game-dir, --keys-file and --output")
    saved, recipe, entry = verified_pack(args.character, args.retained)
    verify_reuse(saved, recipe, entry)
    print(json.dumps({"stage": "retained_pack_verified", "character": args.character,
                      "clips": len(saved["clips"]), "sha256": entry["summary"]["sha256"]}), flush=True)
    if args.run:
        asyncio.run(resume(args, saved, recipe, entry))


if __name__ == "__main__":
    main()
