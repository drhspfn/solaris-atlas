"""Build portable model archives locally without database or R2 credentials."""

import argparse
import asyncio
import json
import os
import re
import tempfile
import time
from pathlib import Path

from wuwa_story.ingestion.model_archives import write_archive


async def build(args):
    from wuwa_story_worker.character_models import build_scene_bundles
    from wuwa_story_worker.model_diagnostics import diagnose_scene_bundles
    from wuwa_story_worker.model_forms import resolve_recipe_forms

    diagnose_mode = bool(getattr(args, "diagnose", False))
    recipe = json.loads(args.recipe.read_text(encoding="utf-8-sig"))
    if recipe.get("format") != "solaris-model-recipe-v1":
        raise ValueError(
            "Download a model recipe from the character import dialog")
    if not diagnose_mode and not re.fullmatch(r"\d+\.\d+\.\d+", args.asset_version or ""):
        raise ValueError("Asset version must use major.minor.patch format")
    if not diagnose_mode and args.output.exists():
        raise ValueError("Output already exists; choose a new archive path")
    if not args.game_dir.is_dir() or not args.keys_file.is_file():
        raise ValueError("Local game directory or keys file is missing")
    for key, value in (("WUWA_FMODEL_PATH", args.fmodel), ("WUWA_TEXTURE_CONVERTER_PATH", args.converter),
                       ("WUWA_BLENDER_PATH", args.blender), ("WUWA_UEFORMAT_PLUGIN", args.ueformat_plugin)):
        if value is not None:
            os.environ[key] = str(value.resolve())
    forms = resolve_recipe_forms(recipe)
    started = time.monotonic()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if getattr(args, "with_blender", False):
        os.environ["WUWA_MODEL_BLENDER_EXPORT"] = "1"
    if diagnose_mode:
        with tempfile.TemporaryDirectory(prefix=".solaris-model-diag-", dir=args.output.parent) as scratch:
            report = await diagnose_scene_bundles(
                Path(scratch), forms,
                local_client={"game": args.game_dir.resolve(), "keys": args.keys_file.resolve(),
                              "processors": args.workers},
                material_variants=getattr(args, "material_variants", False),
                report_progress=lambda stage, **details: print(
                    json.dumps({"stage": stage, **details}), flush=True),
            )
        report["character_key"] = recipe["character_key"]
        report["game_version"] = recipe["game_version"]
        report["seconds"] = round(time.monotonic() - started, 2)
        args.output.write_text(json.dumps(
            report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(json.dumps({"diagnostic_report": str(args.output.resolve()),
                          "forms": report["forms_total"], "entries": report["entries_total"],
                          "errors": report["error_count"], "seconds": report["seconds"]}))
        return
    with tempfile.TemporaryDirectory(prefix=".solaris-model-", dir=args.output.parent) as scratch:
        bundles = await build_scene_bundles(Path(scratch), forms,
                                            include_animations=not args.without_animations,
                                            material_variants=getattr(
                                                args, "material_variants", False),
                                            local_client={"game": args.game_dir.resolve(), "keys": args.keys_file.resolve(),
                                                          "processors": args.workers}, animation_workers=args.workers,
                                            report_progress=lambda stage, **details: print(json.dumps({"stage": stage, **details}), flush=True))
        manifest = await asyncio.to_thread(write_archive, args.output, {
            "character_key": recipe["character_key"], "game_version": recipe["game_version"],
            "asset_version": args.asset_version, "tier": args.tier,
        }, bundles)
    print(json.dumps({"archive": str(args.output.resolve()), "bytes": args.output.stat().st_size,
                      "forms": len(forms), "unique_files": len(manifest["objects"]),
                      "seconds": round(time.monotonic() - started, 2)}))


def main():
    parser = argparse.ArgumentParser(prog="wuwa-model", description=__doc__)
    parser.add_argument("--recipe", type=Path, required=True)
    parser.add_argument("--game-dir", type=Path, required=True)
    parser.add_argument("--keys-file", type=Path, required=True)
    parser.add_argument("--asset-version")
    parser.add_argument("--tier", choices=["sd", "hd", "uhd"], default="hd")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--without-animations", action="store_true")
    parser.add_argument("--material-variants", action="store_true")
    parser.add_argument("--with-blender", action="store_true")
    parser.add_argument("--workers", type=int, choices=range(1,
                        9), default=min(4, os.cpu_count() or 1))
    for name in ["fmodel", "converter", "blender", "ueformat-plugin"]:
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(build(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
