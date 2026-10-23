"""Batch authored event packages and exact numeric media; never mount per voice line."""

import asyncio
import hashlib
import json
import shutil
from pathlib import PurePosixPath

from wuwa_story_worker.archive_index import (
    index_voice_paths,
    indexed_audio_paths,
    voice_filter_batches,
)
from wuwa_story_worker.asset_export import export_assets
from wuwa_story_worker.client_assets import save_json, workspace_lock
from wuwa_story_worker.media_provenance import sha256


def manifest_entries(receipt, plan_id, extractor):
    manifest = json.loads(receipt.read_text(encoding="utf-8"))
    if manifest["job_id"] != plan_id or manifest["extractor_sha256"] != extractor:
        raise ValueError("Character voice export belongs to another client or extractor")
    result = {}
    for item in manifest["files"]:
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in item["path"] or ":" in item["path"]:
            raise ValueError("Unsafe character voice export path")
        result[path.as_posix().removeprefix("Client/Content/")] = (
            receipt.parent / "files" / path, item["sha256"])
    return result


async def prefetch_events(root, sources, cached, fmodel, converter, tool, verify):
    """Return source -> (cooked JSON, exact event originals), sharing one JSON conversion."""
    plan_id = json.loads((root / "plan.json").read_text(encoding="utf-8"))["id"]
    extractor = await asyncio.to_thread(sha256, fmodel)
    identity = hashlib.sha256(json.dumps([sources, extractor, sha256(converter)]).encode()).hexdigest()[:20]
    work = root / "character-voices" / identity
    with workspace_lock(work):
        receipt_cache = work / "event-exports.json"
        entries = dict(cached)
        if receipt_cache.exists():
            for receipt in json.loads(receipt_cache.read_text(encoding="utf-8")):
                if PurePosixPath(receipt).is_absolute() or ".." in PurePosixPath(receipt).parts or "\\" in receipt or ":" in receipt:
                    raise ValueError("Unsafe character event cache receipt")
                path = root / receipt
                entries.update(manifest_entries(path, plan_id, extractor))
        assets = {source: source.split(".")[0].removeprefix("/Game/") for source in sources}
        missing = [asset for asset in assets.values() if asset + ".uasset" not in entries]
        receipts = []
        for batch in voice_filter_batches(missing):
            receipt = await export_assets(root, fmodel, "|".join(batch))
            entries.update(manifest_entries(receipt, plan_id, extractor))
            receipts.append(receipt.relative_to(root).as_posix())
        if receipts:
            existing = json.loads(receipt_cache.read_text(encoding="utf-8")) if receipt_cache.exists() else []
            save_json(receipt_cache, sorted(set(existing + receipts)))
        originals, cooked = {}, {}
        raw, output = work / "files", work / "cooked"
        cooked_receipt = work / "cooked-files.json"
        cooked_hashes = json.loads(cooked_receipt.read_text(encoding="utf-8")) if cooked_receipt.exists() else {}
        package_list = []
        for source, asset in assets.items():
            originals[source] = []
            for extension in (".uasset", ".uexp", ".ubulk", ".bnk"):
                entry = entries.get(asset + extension)
                if entry is not None:
                    original = await asyncio.to_thread(verify, entry)
                    originals[source].append(original)
                    destination = raw / "Client/Content" / (asset + extension)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    if not destination.exists() or sha256(destination) != entry[1]:
                        await asyncio.to_thread(shutil.copyfile, original, destination)
            if not any(path.suffix == ".uasset" for path in originals[source]):
                continue
            # Read old pinned per-event JSON caches as well as the new batch cache.
            legacy = root / "exports" / hashlib.sha256(asset.encode()).hexdigest()[:16] / "cooked-event"
            matches = list(output.rglob(asset.rsplit("/", 1)[-1] + ".json")) if output.exists() else []
            if not matches and legacy.exists():
                matches = list(legacy.rglob(asset.rsplit("/", 1)[-1] + ".json"))
            if len(matches) > 1:
                raise ValueError("Ambiguous cooked character voice event")
            if matches:
                saved_hash = cooked_hashes.get(source)
                if saved_hash is not None and sha256(matches[0]) != saved_hash:
                    raise ValueError("Cached cooked character event changed")
                cooked[source] = matches[0]
            else:
                package_list.append("files/Client/Content/" + asset + ".uasset")
        if package_list:
            output.mkdir(parents=True, exist_ok=True)
            packages = work / "packages.txt"
            packages.write_text("\n".join(package_list), encoding="utf-8")
            await tool([converter.resolve(), "-i", raw.resolve(), "-g", "GAME_WutheringWaves",
                        "-c", packages.resolve(), "-f", "json", "-o", output.resolve(), "-y"],
                       work / "converter.log")
        result = {}
        for source, asset in assets.items():
            if source not in cooked:
                matches = list(output.rglob(asset.rsplit("/", 1)[-1] + ".json"))
                if len(matches) != 1:
                    continue
                cooked[source] = matches[0]
            result[source] = (cooked[source], originals[source])
            cooked_hashes[source] = sha256(cooked[source])
        save_json(cooked_receipt, cooked_hashes)
        return result


def numeric_paths(index, identities):
    paths = indexed_audio_paths(index, [f"{identity}.wem" for identity in identities])
    result = {}
    for path in paths:
        relative = PurePosixPath(path)
        if (relative.is_absolute() or ".." in relative.parts or "\\" in path or ":" in path
                or not relative.stem.isdigit() or int(relative.stem) not in identities
                or relative.suffix.casefold() != ".wem"):
            raise ValueError("Invalid indexed Wwise media path")
        identity = int(relative.stem)
        if identity in result and result[identity] != path:
            raise ValueError(f"Ambiguous exact Wwise media ID: {identity}")
        result[identity] = path
    return result


async def prefetch_media(root, voice_root, identities, cached, fmodel, tool, verify):
    """Reuse A's files(name,path) index; otherwise list each source archive only once."""
    base = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    voice = json.loads((voice_root / "plan.json").read_text(encoding="utf-8"))
    extractor = await asyncio.to_thread(sha256, fmodel)
    identity = hashlib.sha256(json.dumps([base["id"], voice["id"], extractor]).encode()).hexdigest()[:20]
    work = root / "character-voice-media" / identity
    with workspace_lock(work):
        receipt = work / "media.json"
        saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
        result = {}
        for media_id in identities:
            entry = cached.get(f"Aki/WwiseAudio_Generated/Media/{media_id}.wem")
            if entry is not None:
                result[media_id] = (await asyncio.to_thread(verify, entry), base["id"],
                                    f"Client/Content/Aki/WwiseAudio_Generated/Media/{media_id}.wem")
            elif str(media_id) in saved:
                item = saved[str(media_id)]
                path = work / item["path"]
                if (not path.resolve().is_relative_to(work.resolve())
                        or item["download_id"] not in {base["id"], voice["id"]}):
                    raise ValueError("Invalid cached character media origin")
                if path.is_file():
                    result[media_id] = (await asyncio.to_thread(verify, (path, item["sha256"])),
                                        item["download_id"], item["package_path"])
        for label, archive, download_id in (("main", root, base["id"]), ("voice", voice_root, voice["id"])):
            needed = identities - result.keys()
            if not needed:
                break
            index = archive / "archive-index.sqlite"
            if not index.exists():
                # Main-client indices are shared across all characters in the patch.
                index_root = root if label == "main" else work
                log = index_root / ("archive-index.log" if label == "main" else "voice-index.log")
                with workspace_lock(index_root / "character-index-lock"):
                    index = log.with_suffix(".sqlite")
                    if not index.exists():
                        await tool([fmodel.resolve(), (archive / "game").resolve(),
                                    "@" + str((root / "keys.txt").resolve()), "--list"], log, 7200)
                        await asyncio.to_thread(index_voice_paths, log)
            selected = await asyncio.to_thread(numeric_paths, index, needed)
            for batch in voice_filter_batches(selected.values()):
                output = work / label / "files"
                output.mkdir(parents=True, exist_ok=True)
                log = work / f"{label}-export.log"
                await tool([fmodel.resolve(), (archive / "game").resolve(),
                            "@" + str((root / "keys.txt").resolve()), output.resolve(), "|".join(batch)], log, 7200)
                text = log.read_text(encoding="utf-8", errors="replace")
                if "[Fail]" in text or "[Error]" in text:
                    raise RuntimeError("Character Wwise batch export failed; inspect the worker log")
                for media_id, package in selected.items():
                    if package not in batch:
                        continue
                    path = output / package
                    if not path.is_file():
                        continue
                    saved[str(media_id)] = {"path": path.relative_to(work).as_posix(),
                        "package_path": package, "sha256": sha256(path), "download_id": download_id}
                    result[media_id] = (path, download_id, package)
                save_json(receipt, saved)
        return result
